"""Focused tests for Agent 2's read-only SQLite schema discovery."""

from __future__ import annotations

import sqlite3

import pytest

from backend.db_utils import DatabaseConfigurationError, DatabaseReadError
from backend.schema_discovery import get_schema
from sample_data.create_sample_db import create_sample_database


def test_discovers_fixture_tables_columns_types_and_foreign_keys(tmp_path):
    database_path = create_sample_database(tmp_path / "simple.sqlite")

    result = get_schema(database_path)

    assert result["status"] == "success"
    tables = {table["name"]: table for table in result["tables"]}
    assert set(tables) == {
        "customers",
        "products",
        "orders",
        "order_items",
        "customer_tags",
    }
    assert tables["orders"]["row_count"] == 5
    columns = {column["name"]: column for column in tables["orders"]["columns"]}
    assert columns["id"] == {
        "name": "id",
        "type": "INTEGER",
        "nullable": False,
        "pk": True,
    }
    assert columns["customer_id"]["fk_to"] == "customers.id"
    assert {
        (
            foreign_key["column"],
            foreign_key["references_table"],
            foreign_key["references_column"],
        )
        for foreign_key in tables["order_items"]["foreign_keys"]
    } == {("order_id", "orders", "id"), ("product_id", "products", "id")}
    assert "TABLE orders" in result["summary_text"]
    assert result["warnings"] == []


def test_discovers_empty_database_and_empty_table(tmp_path):
    empty_database = tmp_path / "empty.sqlite"
    sqlite3.connect(empty_database).close()
    assert get_schema(empty_database)["tables"] == []

    database_path = tmp_path / "empty_table.sqlite"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE empty_table (id INTEGER PRIMARY KEY, label TEXT)")
        connection.commit()
    finally:
        connection.close()

    result = get_schema(database_path)
    assert result["tables"][0]["name"] == "empty_table"
    assert result["tables"][0]["row_count"] == 0


def test_rejects_missing_database_path(tmp_path):
    with pytest.raises(DatabaseConfigurationError, match="does not exist"):
        get_schema(tmp_path / "missing.sqlite")


def test_accepts_sqlite_config_mapping(tmp_path):
    database_path = create_sample_database(tmp_path / "simple.sqlite")

    result = get_schema({"type": "sqlite", "path": database_path})

    assert result["status"] == "success"
    assert len(result["tables"]) == 5


def test_surfaces_malformed_sqlite_database_error(tmp_path):
    database_path = tmp_path / "not_sqlite.sqlite"
    database_path.write_text("this is not a SQLite database", encoding="utf-8")

    with pytest.raises(DatabaseReadError, match="could not read"):
        get_schema(database_path)


def test_discovery_connection_is_read_only(tmp_path):
    database_path = create_sample_database(tmp_path / "simple.sqlite")
    get_schema(database_path)

    uri = f"file:{database_path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE should_not_exist (id INTEGER)")
    finally:
        connection.close()
