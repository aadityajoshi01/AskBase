"""Runtime-type guard for aggregates and ordering (Agent 3, audit fix).

SQLite's dynamic typing lets SUM/AVG run over a TEXT column and silently treat
non-numeric values as 0, and lets ORDER BY / MIN / MAX sort numeric-looking text
lexicographically ("10" before "9"). Declared column types hide this: a column
declared INTEGER can still store TEXT. This module checks what the data ACTUALLY
is (via `typeof()`) before execution.

Rules, deliberately conservative:
- SUM/AVG over a column with any non-numeric TEXT value -> blocked (the number
  would be wrong, not merely fragile);
- SUM/AVG over numeric-looking TEXT -> warning (coercion works today);
- MIN/MAX/ORDER BY over numeric-looking TEXT -> warning (lexicographic order
  is not the numeric order a reader expects);
- date-like TEXT ordered lexicographically sorts correctly -> no finding;
- anything inside an explicit CAST is the author's stated intent -> skipped.

The guard fails OPEN: if parsing or sampling is impossible, it reports nothing
and lets normal execution surface the database's own error. It never raises.
"""
from __future__ import annotations

import re
import sqlite3

import sqlglot
from sqlglot import exp

_NUMERIC_RE = re.compile(r"^-?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")
_DATE_LIKE_RE = re.compile(
    r"^\d{4}(-\d{2}(-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?)?)?$|^\d{2}/\d{2}/\d{4}$")

_AGGREGATES_BLOCKED = {"SUM", "AVG"}
_AGGREGATES_WARNED = {"MIN", "MAX"}

SAMPLE_LIMIT = 500


def _try_parse(sql: str):
    try:
        node = sqlglot.parse_one(sql, dialect="sqlite")
    except Exception:
        return None
    return node if node is not None and not isinstance(node, exp.Semicolon) else None


def _real_tables(node) -> dict:
    """alias -> table name for every FROM/JOIN source that is a plain table."""
    out = {}
    for table in node.find_all(exp.Table):
        if table.db or table.catalog:  # qualified/attached names: not our namespace
            continue
        out[(table.alias or table.name).lower()] = table.name.lower()
    return out


def _column_text_types(connection, table: str, column: str) -> list[str] | None:
    """Sampled non-NULL text values of table.column; None when unreadable."""
    def q(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'
    try:
        rows = connection.execute(
            f"SELECT DISTINCT {q(column)} AS v FROM {q(table)} "
            f"WHERE typeof({q(column)}) = 'text' AND {q(column)} IS NOT NULL "
            f"LIMIT {SAMPLE_LIMIT}").fetchall()
        text_present = connection.execute(
            f"SELECT COUNT(*) AS n FROM {q(table)} "
            f"WHERE typeof({q(column)}) = 'text'").fetchone()["n"]
    except sqlite3.Error:
        return None
    if not text_present:
        return []
    return [str(row["v"]) for row in rows]


def check_query_types(sql: str, database_path: str) -> tuple[list[str], list[str]]:
    """Return (blocking messages, warning messages) for one validated query."""
    node = _try_parse(sql)
    if node is None:
        return [], []
    tables = _real_tables(node)
    if not tables:
        return [], []

    targets = []
    for agg in node.find_all(exp.AggFunc):
        name = type(agg).__name__.upper()
        if name not in _AGGREGATES_BLOCKED | _AGGREGATES_WARNED:
            continue
        if agg.find(exp.Cast):
            continue
        for col in agg.find_all(exp.Column):
            targets.append((name, col))
    for ordered in node.find_all(exp.Ordered):
        if ordered.find(exp.Cast):
            continue
        col = ordered.find(exp.Column)
        if col is not None:
            targets.append(("ORDER BY", col))
    if not targets:
        return [], []

    blocking: list[str] = []
    warnings: list[str] = []
    sampled: dict[tuple[str, str], list[str] | None] = {}
    try:
        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error:
        return [], []
    try:
        for name, col in targets:
            table = tables.get(col.table.lower()) if col.table else (
                next(iter(tables.values())) if len(tables) == 1 else None)
            if table is None:
                continue
            key = (table, col.name.lower())
            if key not in sampled:
                sampled[key] = _column_text_types(connection, table, col.name)
            values = sampled[key]
            if values is None or not values:
                continue
            numeric = [v for v in values if _NUMERIC_RE.match(v)]
            all_numeric = len(numeric) == len(values)
            readable_label = f"{table}.{col.name}"
            if name in _AGGREGATES_BLOCKED:
                if all_numeric:
                    warnings.append(
                        f"{name}({readable_label}): the column stores numbers as TEXT; "
                        "SQLite coerced them for this aggregate.")
                elif numeric or not all(_DATE_LIKE_RE.match(v) for v in values):
                    blocking.append(
                        f"{name}({readable_label}) refused: {readable_label} stores "
                        "non-numeric TEXT, so the aggregate would silently compute a "
                        "wrong number instead of failing.")
            else:  # MIN/MAX/ORDER BY
                if all_numeric or numeric:
                    warnings.append(
                        f"{name} on {readable_label}: values are numeric-looking TEXT; "
                        "ordering is lexicographic, not numeric (\"10\" before \"9\"). "
                        "CAST the column for a true numeric order.")
    finally:
        try:
            connection.close()
        except sqlite3.Error:
            pass
    return blocking, warnings
