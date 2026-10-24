"""NoSQL document-store support for the gateway (TinyDB-backed).

The pipeline's safety guarantees are SQL/SQLite shaped, so documents are NOT
queried by the SQL engine directly. A NoSQL source is stored as real document
JSON in a TinyDB store (github.com/tinydb/tinydb), then *materialized* into a
SQLite mirror for the existing validate -> execute -> verify chain:

  - scalar fields           -> plain columns;
  - nested objects / scalar arrays -> JSON1 TEXT columns (queryable with
                              json_extract, json_array_length, json_each);
  - arrays of sub-documents -> child tables with a declared FOREIGN KEY ...
                              REFERENCES edge, so Agent 1's FK-graph join
                              guard and Agent 3's validator keep working on
                              flattened documents.

The TinyDB file is the source of truth; the mirror is regenerated on demand.
Extended JSON types ($oid, $date, $numberDecimal, ...) from MongoDB exports
are canonicalized on import.
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
from pathlib import Path

from tinydb import TinyDB

MAX_FLATTEN_DEPTH = 2


def canonicalize(value):
    """MongoDB Extended JSON -> Python natives, recursively."""
    if isinstance(value, dict):
        if len(value) == 1:
            only_key, only_value = next(iter(value.items()))
            if only_key == "$numberDecimal":
                return float(only_value)
            if only_key in ("$oid", "$numberInt", "$numberLong"):
                return str(only_value) if only_key == "$oid" else int(only_value)
            if only_key == "$date":
                if isinstance(only_value, dict):
                    return _iso(int(only_value.get("$numberLong", 0)))
                return str(only_value)
        return {k: canonicalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [canonicalize(v) for v in value]
    return value


def _iso(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc) \
        .strftime("%Y-%m-%d %H:%M:%S")


def _safe_col(name) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(name))
    if not re.match(r"^[A-Za-z_]", cleaned):
        cleaned = "f_" + cleaned
    return cleaned.lower()


def _read_document_file(json_path) -> list:
    """Accept a JSON array or newline-delimited JSON (Mongo exports are NDJSON)."""
    text = Path(json_path).read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raw = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(raw, list):
        raise ValueError("document dataset must be a JSON array or NDJSON lines")
    return raw


def import_documents(json_path, store_path, collection: str = "documents") -> int:
    """Store documents (Extended-JSON aware) into a TinyDB store."""
    raw = _read_document_file(json_path)
    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    if store_path.exists():
        store_path.unlink()
    db = TinyDB(store_path)
    db.table(collection).insert_multiple(canonicalize(doc) for doc in raw)
    db.close()
    return len(raw)


def _declare(entry: dict, col: str, ctype: str) -> None:
    prev = entry["columns"].get(col)
    if prev is None or prev == "TEXT":
        entry["columns"][col] = ctype


def _scalar_type(value) -> str:
    if isinstance(value, float):
        return "REAL"
    if isinstance(value, int) and not isinstance(value, bool):
        return "INTEGER"
    return "TEXT"


def _flatten(docs: list, root: str) -> dict:
    """BFS flatten: table -> {columns, rows, parent_table, parent_ref_col, pk}."""
    tables: dict[str, dict] = {}
    counter = 0
    work = [(root, [("_id_missing_%d" % i, doc) for i, doc in enumerate(docs)])]
    root_entry = {"columns": {}, "rows": [], "parent": None, "ref_col": None, "pk": "_id"}
    tables[root] = root_entry
    depth = 0
    while work and depth <= MAX_FLATTEN_DEPTH:
        next_work = []
        for table, items in work:
            entry = tables[table]
            for given_id, doc in items:
                row = {}
                if entry["ref_col"]:
                    row[entry["ref_col"]] = given_id
                own_id_col = f"{table}_pk"
                if depth >= 1:
                    counter += 1
                    own_id = f"{table}_{counter}"
                    row[own_id_col] = own_id
                    _declare(entry, own_id_col, "TEXT")
                else:
                    own_id = doc.get("_id") or given_id
                    row["_id"] = own_id
                    _declare(entry, "_id", "TEXT")
                for key, value in doc.items():
                    if key == "_id":
                        continue
                    col = _safe_col(key)
                    if value is None or isinstance(value, (str, int, float, bool)):
                        row[col] = value
                        _declare(entry, col, _scalar_type(value))
                    elif isinstance(value, list) and value and all(
                            isinstance(v, dict) for v in value):
                        row[col] = json.dumps(value)
                        _declare(entry, col, "TEXT")
                        child = f"{table}_{col}"
                        if child not in tables:
                            tables[child] = {"columns": {}, "rows": [],
                                             "parent": table,
                                             "ref_col": f"{table}_id" if depth == 0
                                             else f"{table}_pk_ref",
                                             "pk": None}
                            if depth == 0:
                                tables[child]["ref_declares"] = True
                        ref = tables[child]["ref_col"]
                        _declare(tables[child], ref, "TEXT")
                        next_work.append((child, [(own_id, v) for v in value]))
                    else:
                        row[col] = json.dumps(value) if isinstance(value, (dict, list)) \
                            else str(value)
                        _declare(entry, col, "TEXT")
                entry["rows"].append(row)
        work = next_work
        depth += 1
    # child tables created before root row existed may miss ref declaration; fix:
    for name, entry in tables.items():
        if entry["parent"]:
            _declare(entry, entry["ref_col"], "TEXT")
    return tables


def materialize(store_path, collection: str, sqlite_path) -> dict:
    """Rebuild the SQLite mirror from the TinyDB store. Returns {table: info}."""
    store_path, sqlite_path = Path(store_path), Path(sqlite_path)
    db = TinyDB(store_path)
    docs = db.table(collection).all()
    db.close()

    tables = _flatten(docs, collection)
    sqlite_path.unlink(missing_ok=True)
    con = sqlite3.connect(sqlite_path)
    summary = {}
    for table, entry in tables.items():
        names = list(entry["columns"])
        parts = []
        for name in names:
            ddl = f'"{name}" {entry["columns"][name]}'
            if table == collection and name == "_id":
                ddl += " PRIMARY KEY"
            parts.append(ddl)
        if entry["parent"]:
            parent = entry["parent"]
            parent_pk = "_id" if parent == collection else f"{parent}_pk"
            parts.append(f'FOREIGN KEY ("{entry["ref_col"]}") '
                         f'REFERENCES "{parent}" ("{parent_pk}")')
        con.execute(f'CREATE TABLE "{table}" ({", ".join(parts)})')
        con.executemany(
            f'INSERT INTO "{table}" VALUES ({", ".join("?" * len(names))})',
            [[row.get(n) for n in names] for row in entry["rows"]])
        summary[table] = {"row_count": len(entry["rows"]), "columns": names}
    con.commit()
    con.close()
    return summary


def rebuild_dataset(raw_json, store_path, collection: str, sqlite_path) -> dict:
    """Import raw documents into the TinyDB store and re-materialize the mirror."""
    imported = import_documents(raw_json, store_path, collection)
    summary = materialize(store_path, collection, sqlite_path)
    return {"imported_documents": imported, "tables": summary}
