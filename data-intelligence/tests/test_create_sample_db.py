"""Tests for the deterministic simple SQLite fixture."""

from __future__ import annotations

import sqlite3

from sample_data.create_sample_db import create_sample_database


def _connect(path):
    return sqlite3.connect(path)


def test_creates_expected_tables_and_row_counts(tmp_path):
    database_path = create_sample_database(tmp_path / "simple.sqlite")

    connection = _connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "customers",
            "products",
            "orders",
            "order_items",
            "customer_tags",
        } <= tables
        assert connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 5
        assert connection.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 5
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 5
        assert connection.execute("SELECT COUNT(*) FROM order_items").fetchone()[0] == 6
    finally:
        connection.close()


def test_declares_relationships_and_keeps_one_orphan_for_profiling(tmp_path):
    database_path = create_sample_database(tmp_path / "simple.sqlite")

    connection = _connect(database_path)
    try:
        relationships = {
            (table, row[3]): (row[2], row[4])
            for table in ("orders", "order_items", "customer_tags")
            for row in connection.execute(f"PRAGMA foreign_key_list({table})")
        }
        assert relationships[("orders", "customer_id")] == ("customers", "id")
        assert relationships[("order_items", "order_id")] == ("orders", "id")
        assert relationships[("order_items", "product_id")] == ("products", "id")
        assert relationships[("customer_tags", "customer_id")] == ("customers", "id")
        assert len(connection.execute("PRAGMA foreign_key_check").fetchall()) == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM order_items WHERE order_id = 999"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_contains_known_messy_values(tmp_path):
    database_path = create_sample_database(tmp_path / "simple.sqlite")

    connection = _connect(database_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM customers WHERE email = 'asha@example.com'"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM products WHERE typeof(unit_price) = 'text'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM order_items WHERE quantity < 0"
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT customer_id, tag, created_at
                FROM customer_tags
                GROUP BY customer_id, tag, created_at
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_generation_is_reproducible(tmp_path):
    first = create_sample_database(tmp_path / "first.sqlite")
    second = create_sample_database(tmp_path / "second.sqlite")

    assert first.read_bytes() == second.read_bytes()
