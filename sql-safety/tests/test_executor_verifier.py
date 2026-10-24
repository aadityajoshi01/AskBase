"""Gateway-conformance tests (written during Agent 1 integration, 2026-10-24).

These are not a second validator/executor suite — Agent 3 owns those. They pin the
narrow behaviour `gateway/orchestrator.py` depends on, so a future change to the
envelope shape or error vocabulary fails here with the gateway's name on it.
"""
import sqlite3

import pytest

from backend.query_executor import ENVELOPE_KEYS, execute_query
from backend.result_verifier import VERIFIER_KEYS, verify_result

ROWS = [(1, "West", 100.0), (2, "East", 50.0), (3, "West", 25.0)]


@pytest.fixture()
def database(tmp_path):
    path = tmp_path / "test.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE orders (id INTEGER, region TEXT, total REAL)")
    connection.executemany("INSERT INTO orders VALUES (?, ?, ?)", ROWS)
    connection.commit()
    connection.close()
    return str(path)


def envelope(**overrides) -> dict:
    """A complete execution envelope with only the fields under test changed."""
    base = dict.fromkeys(ENVELOPE_KEYS)
    base.update({"status": "success", "sql": "SELECT 1", "dialect": "sqlite",
                 "columns": ["a"], "rows": [[1]], "row_count": 1,
                 "truncated": False, "warnings": [], "elapsed_ms": 1.0, "error": None})
    return {**base, **overrides}


def row_count(database: str) -> int:
    connection = sqlite3.connect(database)
    try:
        return connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    finally:
        connection.close()


def test_success_carries_every_envelope_key(database):
    result = execute_query("SELECT region, SUM(total) AS s FROM orders GROUP BY region",
                           {"path": database})
    assert result["status"] == "success"
    assert set(result) == set(ENVELOPE_KEYS)
    assert result["columns"] == ["region", "s"]
    assert sorted(row[0] for row in result["rows"]) == ["East", "West"]
    assert result["row_count"] == len(result["rows"]) == 2
    assert result["truncated"] is False
    assert result["error"] is None


def test_the_run_sql_is_the_normalized_text(database):
    result = execute_query("select   region from orders", {"path": database})
    assert result["sql"] != "select   region from orders"
    assert result["sql"].lower().startswith("select")


def test_write_is_rejected_without_touching_the_database(database):
    assert execute_query("DELETE FROM orders", {"path": database})["status"] == "error"
    assert row_count(database) == len(ROWS)


def test_missing_table_fails_closed_without_leaking_driver_text(database):
    result = execute_query("SELECT * FROM nope", {"path": database})
    assert result["status"] == "error"
    assert result["error"]["type"] == "invalid_sql"
    assert "no such table" not in result["error"]["message"]
    assert database not in result["error"]["message"]


def test_max_rows_and_timeout_come_from_the_config(database):
    result = execute_query("SELECT * FROM orders",
                           {"path": database, "max_rows": 2, "timeout_s": 5})
    assert result["status"] == "success"
    assert result["row_count"] == 2
    assert result["truncated"] is True
    assert any("truncat" in w.casefold() for w in result["warnings"])


def test_out_of_bounds_config_is_refused_before_opening(database):
    result = execute_query("SELECT * FROM orders", {"path": database, "max_rows": 10**9})
    assert result["status"] == "error"
    assert result["error"]["type"] == "invalid_config"


def test_missing_file_reports_a_controlled_reason():
    result = execute_query("SELECT 1", {"path": "C:/definitely/not/here.db"})
    assert result["status"] == "error"
    assert result["error"]["type"] == "database_not_found"
    assert "definitely" not in result["error"]["message"]


def test_verify_passes_on_a_real_envelope(database):
    verdict = verify_result("regions?", execute_query("SELECT region FROM orders",
                                                     {"path": database}))
    assert set(verdict) == set(VERIFIER_KEYS)
    assert verdict["passed"] is True
    assert verdict["checks_passed"]
    assert verdict["error"] is None


def test_verify_cannot_pass_a_truncated_result_that_says_nothing_about_it():
    verdict = verify_result("q", envelope(truncated=True))
    assert verdict["passed"] is False


def test_verify_passes_a_disclosed_truncated_result():
    verdict = verify_result("q", envelope(
        truncated=True, row_count=1,
        warnings=["Results were truncated to the first 1 rows; more matching rows exist."]))
    assert verdict["passed"] is True


def test_verify_passes_an_empty_success():
    assert verify_result("q", envelope(columns=["a"], rows=[], row_count=0))["passed"] is True


def test_verify_fails_on_row_column_mismatch():
    verdict = verify_result("q", envelope(columns=["a", "b"], rows=[[1]], row_count=1))
    assert verdict["passed"] is False
    assert verdict["failures"]


def test_verify_fails_on_a_row_count_that_lies():
    verdict = verify_result("q", envelope(rows=[[1], [2]], row_count=5))
    assert verdict["passed"] is False


def test_verify_refuses_a_failed_execution(database):
    verdict = verify_result("q", execute_query("DROP TABLE orders", {"path": database}))
    assert verdict["passed"] is False
    assert "not verified" in verdict["explanation"].casefold() or verdict["failures"]
