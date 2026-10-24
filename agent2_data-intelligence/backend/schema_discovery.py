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


def _discover_tables(connection: sqlite3.Connection) -> list[TableSchema]:
    tables: list[TableSchema] = []
    for table_name, kind in _list_objects(connection):
        row_count = _table_row_count(connection, table_name)
        foreign_keys = _table_foreign_keys(connection, table_name)
        foreign_key_targets = {
            foreign_key.column: (
                f"{foreign_key.references_table}.{foreign_key.references_column}"
            )
            for foreign_key in foreign_keys
        }
        columns = [
            replace(column, fk_to=foreign_key_targets.get(column.name))
            for column in _table_columns(connection, table_name)
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
                        message=f"Discovered {kind} with {row_count} rows.",
                        table=table_name,
                    )
                ],
            )
        )
    return tables


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
) -> tuple[list[TableSchema], str]:
    """Discover tables using an already-open read-only connection."""

    tables = _discover_tables(connection)
    return tables, _summary_text(tables)


def get_schema(
    database_config: str | Path | Mapping[str, object],
) -> dict[str, object]:
    """Return structured schema metadata for an arbitrary SQLite database."""

    with read_only_connection(database_config) as connection:
        tables, summary_text = discover_schema(connection)

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
        "warnings": [],
        "errors": [],
        "summary_text": summary_text,
    }
