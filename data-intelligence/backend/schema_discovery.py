"""Read-only SQLite schema discovery for AskBase."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from .db_utils import quote_identifier, read_only_connection
from .models import (
    ColumnSchema,
    ForeignKeyDefinition,
    Observation,
    TableSchema,
)


def _list_objects(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = connection.execute(
        """
        SELECT name, type
        FROM sqlite_master
        WHERE type IN ('table', 'view')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [(str(row["name"]), str(row["type"])) for row in rows]


def _table_row_count(connection: sqlite3.Connection, table_name: str) -> int:
    identifier = quote_identifier(table_name)
    row = connection.execute(f"SELECT COUNT(*) AS row_count FROM {identifier}").fetchone()
    return int(row["row_count"])


def _table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> list[ColumnSchema]:
    identifier = quote_identifier(table_name)
    rows = connection.execute(f"PRAGMA table_info({identifier})").fetchall()
    return [
        ColumnSchema(
            name=str(row["name"]),
            type=str(row["type"] or ""),
            nullable=not bool(row["notnull"] or row["pk"]),
            pk=bool(row["pk"]),
        )
        for row in rows
    ]


def _table_foreign_keys(
    connection: sqlite3.Connection,
    table_name: str,
) -> list[ForeignKeyDefinition]:
    identifier = quote_identifier(table_name)
    rows = connection.execute(f"PRAGMA foreign_key_list({identifier})").fetchall()
    return [
        ForeignKeyDefinition(
            column=str(row["from"]),
            references_table=str(row["table"]),
            references_column=str(row["to"]) if row["to"] is not None else None,
        )
        for row in rows
    ]


def _discover_tables(
    connection: sqlite3.Connection,
) -> tuple[list[TableSchema], list[str]]:
    """Discover every table/view; a broken view never aborts discovery.

    Counting rows in a view whose base objects were dropped raises sqlite3.Error;
    the contract-facing functions must return, so the object is reported as
    unreadable with a warning instead.
    """
    tables: list[TableSchema] = []
    unreadable: list[str] = []
    for table_name, kind in _list_objects(connection):
        # PRAGMA table_info and row counting both validate a view's definition,
        # so any read of a broken view raises; report it instead of crashing.
        try:
            row_count: int | None = _table_row_count(connection, table_name)
            foreign_keys = _table_foreign_keys(connection, table_name)
            raw_columns = _table_columns(connection, table_name)
        except sqlite3.Error:
            row_count = None
            foreign_keys = []
            raw_columns = []
            unreadable.append(
                f"'{table_name}' ({kind}) could not be read — its definition "
                "references missing objects; queries against it will fail."
            )
        foreign_key_targets = {
            foreign_key.column: (
                f"{foreign_key.references_table}.{foreign_key.references_column}"
            )
            for foreign_key in foreign_keys
        }
        columns = [
            replace(column, fk_to=foreign_key_targets.get(column.name))
            for column in raw_columns
        ]
        tables.append(
            TableSchema(
                name=table_name,
                kind=kind,
                row_count=row_count,
                columns=columns,
                foreign_keys=foreign_keys,
                observations=[
                    Observation(
                        code="table_discovered",
                        message=(f"Discovered {kind} with {row_count} rows."
                                 if row_count is not None else
                                 f"Discovered {kind} that could not be read."),
                        table=table_name,
                    )
                ],
            )
        )
    return tables, unreadable


def _summary_text(tables: list[TableSchema]) -> str:
    lines: list[str] = []
    for table in tables:
        columns = []
        for column in table.columns:
            flags = []
            if column.pk:
                flags.append("PK")
            if not column.nullable:
                flags.append("NOT NULL")
            suffix = f" {' '.join(flags)}" if flags else ""
            columns.append(f"{column.name} {column.type or 'UNDECLARED'}{suffix}")
        lines.append(f"TABLE {table.name} ({', '.join(columns)})")
        for foreign_key in table.foreign_keys:
            target = f"{foreign_key.references_table}.{foreign_key.references_column or '?'}"
            lines.append(f"  FK {table.name}.{foreign_key.column} -> {target}")
    return "\n".join(lines)


def discover_schema(
    connection: sqlite3.Connection,
) -> tuple[list[TableSchema], str, list[str]]:
    """Discover tables using an already-open read-only connection.

    Returns (tables, summary_text, unreadable_object_warnings).
    """

    tables, unreadable = _discover_tables(connection)
    return tables, _summary_text(tables), unreadable


def get_schema(
    database_config: str | Path | Mapping[str, object],
) -> dict[str, object]:
    """Return structured schema metadata for an arbitrary SQLite database."""

    with read_only_connection(database_config) as connection:
        tables, summary_text, unreadable = discover_schema(connection)

    return {
        "status": "success",
        "dialect": "sqlite",
        "tables": [table.to_dict() for table in tables],
        "observations": [
            {
                "code": "database_discovered",
                "message": f"Discovered {len(tables)} SQLite tables or views.",
                "table": None,
                "column": None,
            }
        ],
        "warnings": list(unreadable),
        "errors": [],
        "summary_text": summary_text,
    }
