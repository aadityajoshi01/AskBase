"""Read-only, SQL-first basic data-quality profiling for SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Mapping

from .db_utils import quote_identifier, read_only_connection
from .models import ForeignKeyViolation, Observation, TableSchema, WarningRecord
from .schema_discovery import discover_schema


def _column_profile(
    connection: sqlite3.Connection,
    table: TableSchema,
    column_name: str,
) -> tuple[dict[str, object], list[WarningRecord]]:
    table_identifier = quote_identifier(table.name)
    column_identifier = quote_identifier(column_name)
    row_count = table.row_count

    null_count = int(
        connection.execute(
            f"SELECT COUNT(*) AS count FROM {table_identifier} "
            f"WHERE {column_identifier} IS NULL"
        ).fetchone()["count"]
    )
    distinct_count = int(
        connection.execute(
            f"SELECT COUNT(DISTINCT {column_identifier}) AS count "
            f"FROM {table_identifier}"
        ).fetchone()["count"]
    )
    type_rows = connection.execute(
        f"""
        SELECT typeof({column_identifier}) AS sqlite_type, COUNT(*) AS count
        FROM {table_identifier}
        GROUP BY typeof({column_identifier})
        ORDER BY sqlite_type
        """
    ).fetchall()
    sqlite_type_counts = {
        str(row["sqlite_type"]): int(row["count"]) for row in type_rows
    }
    non_null_types = {
        sqlite_type: count
        for sqlite_type, count in sqlite_type_counts.items()
        if sqlite_type != "null"
    }

    warnings: list[WarningRecord] = []
    if null_count:
        warnings.append(
            WarningRecord(
                code="null_values",
                severity="warning",
                message=f"{null_count} of {row_count} rows are NULL.",
                table=table.name,
                column=column_name,
                details={"null_count": null_count, "denominator": row_count},
            )
        )
    if len(non_null_types) > 1:
        warnings.append(
            WarningRecord(
                code="mixed_sqlite_types",
                severity="warning",
                message="Non-NULL values use multiple SQLite runtime types.",
                table=table.name,
                column=column_name,
                details={"sqlite_type_counts": non_null_types},
            )
        )

    profile = {
        "name": column_name,
        "type": next(
            column.type for column in table.columns if column.name == column_name
        ),
        "nullable": next(
            column.nullable for column in table.columns if column.name == column_name
        ),
        "pk": next(column.pk for column in table.columns if column.name == column_name),
        "null_count": null_count,
        "null_percentage": (null_count / row_count * 100.0) if row_count else 0.0,
        "distinct_count": distinct_count,
        "sqlite_type_counts": sqlite_type_counts,
        "observations": [
            Observation(
                code="column_profiled",
                message=(
                    f"Profiled {row_count} rows; {distinct_count} distinct non-NULL values."
                ),
                table=table.name,
                column=column_name,
            ).to_dict()
        ],
        "warnings": [warning.to_dict() for warning in warnings],
    }
    return profile, warnings


def _duplicate_counts(
    connection: sqlite3.Connection,
    table: TableSchema,
) -> tuple[int, int]:
    if not table.columns or table.row_count == 0:
        return 0, 0

    table_identifier = quote_identifier(table.name)
    column_list = ", ".join(quote_identifier(column.name) for column in table.columns)
    row = connection.execute(
        f"""
        SELECT
            COUNT(*) AS duplicate_groups,
            COALESCE(SUM(group_count - 1), 0) AS duplicate_rows
        FROM (
            SELECT COUNT(*) AS group_count
            FROM {table_identifier}
            GROUP BY {column_list}
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()
    return int(row["duplicate_rows"] or 0), int(row["duplicate_groups"] or 0)


def _foreign_key_violations(
    connection: sqlite3.Connection,
    tables: list[TableSchema],
) -> list[ForeignKeyViolation]:
    column_by_fk_id: dict[tuple[str, int], str | None] = {}
    for table in tables:
        table_identifier = quote_identifier(table.name)
        for row in connection.execute(
            f"PRAGMA foreign_key_list({table_identifier})"
        ).fetchall():
            key = (table.name, int(row["id"]))
            column_by_fk_id.setdefault(key, str(row["from"]))

    violations: list[ForeignKeyViolation] = []
    for table in tables:
        if table.kind != "table":
            continue
        table_identifier = quote_identifier(table.name)
        for row in connection.execute(
            f"PRAGMA foreign_key_check({table_identifier})"
        ).fetchall():
            violations.append(
                ForeignKeyViolation(
                    table=str(row["table"]),
                    rowid=int(row["rowid"]) if row["rowid"] is not None else None,
                    parent_table=str(row["parent"]),
                    foreign_key_id=int(row["fkid"]),
                    column=column_by_fk_id.get((str(row["table"]), int(row["fkid"]))),
                )
            )
    return violations


def profile_database(
    database_config: str | Path | Mapping[str, object],
) -> dict[str, object]:
    """Return basic, read-only data-quality metrics for a SQLite database."""

    with read_only_connection(database_config) as connection:
        tables, summary_text, unreadable = discover_schema(connection)
        all_warnings: list[WarningRecord] = []
        profile_tables: list[dict[str, object]] = []

        for message in unreadable:
            all_warnings.append(
                WarningRecord(code="unreadable_object", severity="warning",
                              message=message)
            )

        for table in tables:
            if table.row_count is None:
                # Broken view: every read against it raises, so profile nothing further.
                profile_tables.append(
                    {
                        "name": table.name,
                        "kind": table.kind,
                        "row_count": None,
                        "column_count": len(table.columns),
                        "columns": [],
                        "foreign_keys": [
                            foreign_key.to_dict() for foreign_key in table.foreign_keys
                        ],
                        "duplicate_row_count": 0,
                        "duplicate_group_count": 0,
                        "observations": [],
                        "warnings": [w.to_dict() for w in table.warnings],
                    }
                )
                continue
            duplicate_row_count, duplicate_group_count = _duplicate_counts(
                connection, table
            )
            table_warnings: list[WarningRecord] = []
            if table.row_count == 0:
                table_warnings.append(
                    WarningRecord(
                        code="empty_table",
                        severity="warning",
                        message="The table contains no rows.",
                        table=table.name,
                    )
                )
            if duplicate_row_count:
                table_warnings.append(
                    WarningRecord(
                        code="duplicate_rows",
                        severity="warning",
                        message=(
                            f"{duplicate_row_count} rows belong to duplicate groups "
                            f"across all columns."
                        ),
                        table=table.name,
                        details={
                            "duplicate_row_count": duplicate_row_count,
                            "duplicate_group_count": duplicate_group_count,
                        },
                    )
                )

            columns: list[dict[str, object]] = []
            for column in table.columns:
                column_profile, column_warnings = _column_profile(
                    connection, table, column.name
                )
                columns.append(column_profile)
                table_warnings.extend(column_warnings)

            all_warnings.extend(table_warnings)
            profile_tables.append(
                {
                    "name": table.name,
                    "kind": table.kind,
                    "row_count": table.row_count,
                    "column_count": len(table.columns),
                    "columns": columns,
                    "foreign_keys": [
                        foreign_key.to_dict() for foreign_key in table.foreign_keys
                    ],
                    "duplicate_row_count": duplicate_row_count,
                    "duplicate_group_count": duplicate_group_count,
                    "observations": [
                        Observation(
                            code="table_profiled",
                            message=f"Profiled {table.row_count} rows.",
                            table=table.name,
                        ).to_dict()
                    ],
                    "warnings": [warning.to_dict() for warning in table_warnings],
                }
            )

        violations = _foreign_key_violations(connection, tables)

    for violation in violations:
        warning = WarningRecord(
            code="foreign_key_violation",
            severity="warning",
            message=(
                f"A row in {violation.table} references a missing "
                f"{violation.parent_table} row."
            ),
            table=violation.table,
            column=violation.column,
            details={
                "rowid": violation.rowid,
                "parent_table": violation.parent_table,
                "foreign_key_id": violation.foreign_key_id,
            },
        )
        all_warnings.append(warning)

    status = "warning" if all_warnings else "success"
    return {
        "status": status,
        "dialect": "sqlite",
        "tables": profile_tables,
        "foreign_key_violations": [violation.to_dict() for violation in violations],
        "observations": [
            {
                "code": "database_profiled",
                "message": f"Profiled {len(profile_tables)} SQLite tables or views.",
                "table": None,
                "column": None,
            }
        ],
        "warnings": [warning.to_dict() for warning in all_warnings],
        "errors": [],
        "summary_text": summary_text,
    }
