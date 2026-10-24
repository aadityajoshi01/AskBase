"""Focused tests for Agent 2's basic SQLite data-quality profiler."""

from __future__ import annotations

import sqlite3

from backend.data_profiler import profile_database
from sample_data.create_sample_db import create_sample_database


def _table(result, name):
    return next(table for table in result["tables"] if table["name"] == name)


def _column(table, name):
    return next(column for column in table["columns"] if column["name"] == name)


def test_profiles_row_counts_nulls_mixed_types_duplicates_and_orphan(tmp_path):
    database_path = create_sample_database(tmp_path / "simple.sqlite")

    result = profile_database(database_path)

    assert result["status"] == "warning"
    assert result["dialect"] == "sqlite"
    assert len(result["foreign_key_violations"]) == 1
    violation = result["foreign_key_violations"][0]
    assert violation["table"] == "order_items"
    assert violation["parent_table"] == "orders"
    assert violation["column"] == "order_id"

    customers = _table(result, "customers")
    assert customers["row_count"] == 5
    assert _column(customers, "email")["null_count"] == 1
    assert _column(customers, "email")["distinct_count"] == 3
    assert _column(customers, "email")["null_percentage"] == 20.0

    products = _table(result, "products")
    unit_price = _column(products, "unit_price")
    assert unit_price["sqlite_type_counts"]["text"] == 1
    assert any(
        warning["code"] == "mixed_sqlite_types" for warning in unit_price["warnings"]
    )

    tags = _table(result, "customer_tags")
    assert tags["duplicate_row_count"] == 1
    assert tags["duplicate_group_count"] == 1
    assert any(
        warning["code"] == "duplicate_rows" for warning in tags["warnings"]
    )
    assert any(
        warning["code"] == "foreign_key_violation"
        and warning["table"] == "order_items"
        for warning in result["warnings"]
    )


def test_profiles_empty_table_with_zero_denominator(tmp_path):
    database_path = tmp_path / "empty_table.sqlite"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE empty_table (id INTEGER, label TEXT)")
        connection.commit()
    finally:
        connection.close()

    result = profile_database(database_path)

    table = _table(result, "empty_table")
    assert table["row_count"] == 0
    assert _column(table, "label")["null_count"] == 0
    assert _column(table, "label")["null_percentage"] == 0.0
    assert table["duplicate_row_count"] == 0
    assert any(warning["code"] == "empty_table" for warning in table["warnings"])


def test_profiles_normal_database_without_warnings(tmp_path):
    database_path = tmp_path / "normal.sqlite"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE numbers (id INTEGER PRIMARY KEY, value INTEGER)")
        connection.executemany("INSERT INTO numbers (value) VALUES (?)", [(1,), (2,), (3,)])
        connection.commit()
    finally:
        connection.close()

    result = profile_database(database_path)

    assert result["status"] == "success"
    assert result["warnings"] == []
    table = _table(result, "numbers")
    assert table["row_count"] == 3
    assert _column(table, "value")["sqlite_type_counts"] == {"integer": 3}
