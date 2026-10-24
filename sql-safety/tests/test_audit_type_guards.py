"""Regression tests for the audit's data-safety findings:
- SUM/AVG/MIN/MAX/ORDER BY silently reading mistyped TEXT (type guard),
- multi-statement rejection and valid CTE handling (validator, locked in),
- postal_code stored as TEXT with leading zeros preserved (demo datasets)."""
import sqlite3

import pytest

from backend.query_executor import execute_query
from backend.sql_validator import validate_sql
from backend.type_guards import check_query_types


def _db(tmp_path, values):
    path = tmp_path / "types.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, amount TEXT, day TEXT)")
    con.executemany("INSERT INTO events (amount, day) VALUES (?, ?)", values)
    con.commit()
    con.close()
    return {"type": "sqlite", "path": str(path)}


TEXTY = [("12.5", "2024-01-05"), ("abc", "2024-01-06"), ("3", "2024-02-01")]
NUMERIC_TEXT = [("10", "2024-01-05"), ("9", "2024-01-06"), ("100", "2024-02-01")]


# --- item 3: type guard ---

def test_sum_over_non_numeric_text_is_blocked(tmp_path):
    blocking, _ = check_query_types("SELECT SUM(amount) FROM events", str(tmp_path / "x"))
    # no db at that path -> guard fails open, proving the fail-open contract
    assert blocking == []
    config = _db(tmp_path, TEXTY)
    out = execute_query("SELECT SUM(amount) FROM events", config)
    assert out["status"] == "error"
    assert out["error"]["type"] == "type_unsafe_aggregate"


def test_avg_over_non_numeric_text_is_blocked(tmp_path):
    out = execute_query("SELECT AVG(amount) FROM events", _db(tmp_path, TEXTY))
    assert out["status"] == "error" and out["error"]["type"] == "type_unsafe_aggregate"


def test_sum_over_numeric_text_succeeds_with_warning(tmp_path):
    out = execute_query("SELECT SUM(amount) FROM events", _db(tmp_path, NUMERIC_TEXT))
    assert out["status"] == "success"
    assert out["rows"][0][0] == 119
    assert any("stores numbers as TEXT" in w for w in out["warnings"])


def test_order_by_numeric_text_warns_about_lexicographic_sort(tmp_path):
    out = execute_query("SELECT amount FROM events ORDER BY amount", _db(tmp_path, NUMERIC_TEXT))
    assert out["status"] == "success"
    assert any("lexicographic" in w for w in out["warnings"])


def test_iso_date_text_orders_cleanly_without_warning(tmp_path):
    out = execute_query("SELECT day FROM events ORDER BY day", _db(tmp_path, TEXTY))
    assert out["status"] == "success"
    assert not any("lexicographic" in w for w in out["warnings"])


def test_explicit_cast_is_respected(tmp_path):
    out = execute_query("SELECT SUM(CAST(amount AS REAL)) FROM events", _db(tmp_path, TEXTY))
    assert out["status"] == "success"


def test_guard_fail_open_on_unresolvable_columns(tmp_path):
    config = _db(tmp_path, TEXTY)
    # ambiguous unqualified column across two sources: the guard refuses to guess,
    # and a resolvable column on a real table is still checked
    blocking, _ = check_query_types(
        "SELECT SUM(amount) FROM events, events AS e2", config["path"])
    assert blocking == []
    blocking, _ = check_query_types(
        "SELECT SUM(e.amount) FROM events e JOIN other o ON o.id = e.id",
        config["path"])
    assert blocking and "non-numeric TEXT" in blocking[0]


# --- item 5: validator behaviour locked in ---

@pytest.mark.parametrize("sql", [
    "SELECT 1; SELECT 2",
    "SELECT id FROM orders; DROP TABLE orders",
    "SELECT 1; DELETE FROM orders; SELECT 2",
])
def test_multi_statement_sql_is_rejected(sql):
    verdict = validate_sql(sql)
    assert verdict["status"] == "error"
    assert verdict["error"]["type"] == "multiple_statements"


def test_valid_cte_queries_pass_validation():
    for sql in [
        "WITH m AS (SELECT 1 AS x) SELECT x FROM m",
        "WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt WHERE x < 5) "
        "SELECT MAX(x) FROM cnt",
    ]:
        verdict = validate_sql(sql)
        assert verdict["status"] == "ok", sql


# --- item 6 postal tests live in agent1 (datasets package) ---

