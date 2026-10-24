"""Tests for backend.query_executor — config validation, envelope, SQLite execution.

No live model, GPU or external server is needed: every database here is a throwaway
SQLite file created under pytest's tmp_path. Execution tests assert both the result
envelope and that the file on disk is byte-for-byte untouched, since the executor
must be read-only.
"""
import os
import sqlite3
import time

import pytest

from backend.query_executor import (
    DEFAULT_MAX_ROWS,
    DEFAULT_TIMEOUT_S,
    ENVELOPE_KEYS,
    EXECUTION_DIALECTS,
    execute_query,
)

VALID_SQL = "SELECT id, region FROM customers ORDER BY id"
INFINITE_COUNT = ("WITH RECURSIVE t(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM t) "
                  "SELECT count(*) FROM t")


def blank_db(tmp_path, name="blank.db"):
    """A real, empty file: enough for path resolution, empty as a database."""
    path = tmp_path / name
    path.write_bytes(b"")
    return str(path)


def config(tmp_path, **overrides):
    cfg = {"dialect": "sqlite", "path": blank_db(tmp_path)}
    cfg.update(overrides)
    return cfg


def sample_db(tmp_path, rows=4, name="payroll.db"):
    """A genuine SQLite database with a customers table; returns (path, bytes)."""
    path = tmp_path / name
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, region TEXT, total NUMERIC)")
    conn.executemany(
        "INSERT INTO customers (id, region, total) VALUES (?, ?, ?)",
        [(i, f"region{i % 3}", (None if i == 3 else i * 10)) for i in range(1, rows + 1)])
    conn.commit()
    conn.close()
    return str(path), path.read_bytes()


# --- envelope -----------------------------------------------------------------

def test_valid_request_returns_full_envelope(tmp_path):
    result = execute_query(VALID_SQL, config(tmp_path, path=sample_db(tmp_path)[0]))
    assert list(result) == list(ENVELOPE_KEYS)
    assert result["status"] == "success"


@pytest.mark.parametrize("sql, cfg", [
    ("SELECT 1", {}),
    ("DROP TABLE customers", {"dialect": "sqlite", "path": "/nope/x.db"}),
    (None, None),
])
def test_envelope_is_identical_on_every_failure_path(sql, cfg):
    result = execute_query(sql, cfg)
    assert set(result) == set(ENVELOPE_KEYS)
    assert result["status"] == "error"


def test_error_outcome_shape_is_typed_dict(tmp_path):
    result = execute_query(VALID_SQL, {"dialect": "sqlite"})
    assert isinstance(result["error"], dict)
    assert set(result["error"]) == {"type", "message"}
    assert result["error"]["message"]


def test_error_defaults_are_empty_not_missing(tmp_path):
    result = execute_query(VALID_SQL, config(tmp_path))
    assert result["columns"] == []
    assert result["rows"] == []
    assert result["row_count"] == 0
    assert result["truncated"] is False
    assert result["warnings"] == []


def test_elapsed_ms_is_positive_on_real_execution(tmp_path):
    result = execute_query(VALID_SQL, config(tmp_path, path=sample_db(tmp_path)[0]))
    assert isinstance(result["elapsed_ms"], (int, float))
    assert result["elapsed_ms"] > 0


def test_execute_query_never_raises_on_garbage(tmp_path):
    good = config(tmp_path, path=sample_db(tmp_path)[0])
    for bad_sql in (None, 42, b"SELECT 1", ["SELECT 1"], "", "SELECT"):
        for bad_cfg in (None, "sqlite", 7, [], {"path": 1}, good):
            result = execute_query(bad_sql, bad_cfg)
            assert isinstance(result, dict)
            assert result["status"] == "error"
            assert result["error"]["type"]


# --- successful execution -----------------------------------------------------

def test_select_returns_columns_rows_and_count(tmp_path):
    path, _ = sample_db(tmp_path, rows=4)
    result = execute_query(VALID_SQL, config(tmp_path, path=path))
    assert result["status"] == "success"
    assert result["error"] is None
    assert result["columns"] == ["id", "region"]
    assert result["row_count"] == 4
    assert len(result["rows"]) == 4
    assert result["rows"][0] == [1, "region1"]


def test_values_and_types_survive_the_round_trip(tmp_path):
    path, _ = sample_db(tmp_path, rows=4)
    sql = "SELECT id, total IS NULL AS is_null, total FROM customers ORDER BY id LIMIT 3"
    result = execute_query(sql, config(tmp_path, path=path))
    assert result["status"] == "success"
    assert [row[1] for row in result["rows"]] == [0, 0, 1]  # third row is NULL
    assert result["rows"][2][2] is None


def test_aggregate_and_join_run(tmp_path):
    path, _ = sample_db(tmp_path, rows=6)
    sql = "SELECT region, COUNT(*) AS n FROM customers GROUP BY region ORDER BY region"
    result = execute_query(sql, config(tmp_path, path=path))
    assert result["columns"] == ["region", "n"]
    assert [row[1] for row in result["rows"]] == [2, 2, 2]


def test_empty_result_is_success_not_error(tmp_path):
    path, _ = sample_db(tmp_path)
    sql = "SELECT id FROM customers WHERE region = 'atlantis'"
    result = execute_query(sql, config(tmp_path, path=path))
    assert result["status"] == "success"
    assert result["error"] is None
    assert result["row_count"] == 0
    assert result["rows"] == []
    assert result["truncated"] is False


def test_success_reports_the_normalized_sql_not_the_raw_string(tmp_path):
    path, _ = sample_db(tmp_path)
    result = execute_query("SELECT id FROM customers; -- trailing note",
                           config(tmp_path, path=path))
    assert result["status"] == "success"
    assert "--" not in result["sql"]
    assert ";" not in result["sql"]


def test_defaults_apply_when_options_are_omitted(tmp_path):
    path, _ = sample_db(tmp_path)
    result = execute_query(VALID_SQL, {"path": path})
    assert result["status"] == "success"


# --- row limits ---------------------------------------------------------------

def test_truncation_caps_rows_and_warns(tmp_path):
    path, _ = sample_db(tmp_path, rows=10)
    result = execute_query(VALID_SQL, config(tmp_path, path=path, max_rows=3))
    assert result["status"] == "success"
    assert result["row_count"] == 3
    assert len(result["rows"]) == 3
    assert result["truncated"] is True
    assert any("3" in w and "truncat" in w.casefold() for w in result["warnings"])


def test_exact_fit_is_not_reported_as_truncated(tmp_path):
    path, _ = sample_db(tmp_path, rows=4)
    result = execute_query(VALID_SQL, config(tmp_path, path=path, max_rows=4))
    assert result["row_count"] == 4
    assert result["truncated"] is False
    assert result["warnings"] == []


def test_row_count_never_exceeds_max_rows(tmp_path):
    path, _ = sample_db(tmp_path, rows=10)
    for limit in (1, 2, 9):
        result = execute_query(VALID_SQL, config(tmp_path, path=path, max_rows=limit))
        assert result["row_count"] <= limit
        assert len(result["rows"]) <= limit


def test_no_truncation_warning_without_truncation(tmp_path):
    path, _ = sample_db(tmp_path, rows=2)
    result = execute_query(VALID_SQL, config(tmp_path, path=path, max_rows=1000))
    assert not any("truncat" in w.casefold() for w in result["warnings"])


# --- read-only guarantee ------------------------------------------------------

def test_rejected_write_leaves_the_database_untouched(tmp_path):
    path, original = sample_db(tmp_path, rows=5)
    result = execute_query("DELETE FROM customers", config(tmp_path, path=path))
    assert result["error"]["type"] == "invalid_sql"
    assert open(path, "rb").read() == original


@pytest.mark.parametrize("stmt", [
    "UPDATE customers SET region = 'hacked'",
    "DROP TABLE customers",
    "INSERT INTO customers (id, region) VALUES (99, 'x')",
])
def test_writes_are_refused_before_execution(tmp_path, stmt):
    path, original = sample_db(tmp_path, rows=3)
    result = execute_query(stmt, config(tmp_path, path=path))
    assert result["status"] == "error"
    assert result["error"]["type"] == "invalid_sql"
    assert open(path, "rb").read() == original


def test_read_only_connection_refuses_writes_even_if_validation_is_bypassed(
        tmp_path, monkeypatch):
    """Defence in depth: the mode=ro connection, not the parser, is the last wall."""
    path, original = sample_db(tmp_path, rows=3)
    import backend.query_executor as qe
    monkeypatch.setattr(qe, "validate_sql", lambda sql, dialect: {
        "valid": True, "normalized_sql": "UPDATE customers SET region = 'hacked'"})
    result = execute_query("UPDATE customers SET region = 'hacked'",
                           config(tmp_path, path=path))
    assert result["status"] == "error"
    assert result["error"]["type"] == "permission_denied"
    assert open(path, "rb").read() == original


def test_successful_read_leaves_the_database_untouched(tmp_path):
    path, original = sample_db(tmp_path, rows=6)
    result = execute_query(VALID_SQL, config(tmp_path, path=path))
    assert result["status"] == "success"
    assert open(path, "rb").read() == original


def test_missing_database_is_reported_and_never_created(tmp_path):
    path = str(tmp_path / "absent.db")
    result = execute_query("SELECT 1", config(tmp_path, path=path))
    assert result["error"]["type"] == "database_not_found"
    assert not os.path.exists(path)


def test_non_database_file_is_not_readable_as_one(tmp_path):
    garbage = tmp_path / "notes.db"
    garbage.write_bytes(b"this is definitely not a sqlite database")
    result = execute_query("SELECT 1", config(tmp_path, path=str(garbage)))
    assert result["error"]["type"] == "database_unavailable"


# --- timeouts -----------------------------------------------------------------

def test_timeout_returns_a_controlled_error(tmp_path):
    path, original = sample_db(tmp_path, rows=3)
    started = time.monotonic()
    result = execute_query(INFINITE_COUNT, config(tmp_path, path=path, timeout_s=0.1))
    wall = time.monotonic() - started
    assert result["status"] == "error"
    assert result["error"]["type"] == "timeout"
    assert "timeout_s" in result["error"]["message"]
    assert wall < 3, f"deadline overshot badly: {wall:.2f}s"
    assert open(path, "rb").read() == original


def test_generous_timeout_lets_a_normal_query_finish(tmp_path):
    path, _ = sample_db(tmp_path, rows=50)
    result = execute_query(VALID_SQL, config(tmp_path, path=path, timeout_s=DEFAULT_TIMEOUT_S))
    assert result["status"] == "success"


def test_row_cap_can_finish_a_long_scan_before_the_deadline(tmp_path):
    """Documents the interaction honestly: fetchmany stops early, so no timeout fires."""
    path, _ = sample_db(tmp_path, rows=200)
    sql = "WITH RECURSIVE t(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM t) SELECT x FROM t"
    result = execute_query(sql, config(tmp_path, path=path, timeout_s=0.05, max_rows=5))
    assert result["status"] == "success"
    assert result["truncated"] is True


# --- database_config: dialect and path ---------------------------------------

def test_dialect_defaults_to_sqlite(tmp_path):
    path, _ = sample_db(tmp_path)
    assert execute_query(VALID_SQL, {"path": path})["dialect"] == "sqlite"


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "bigquery", "duckdb"])
def test_unsupported_dialects_are_rejected(tmp_path, dialect):
    cfg = {"dialect": dialect, "path": blank_db(tmp_path)}
    assert execute_query(VALID_SQL, cfg)["error"]["type"] == "unsupported_dialect"
    assert dialect not in EXECUTION_DIALECTS


def test_dialect_must_be_a_string(tmp_path):
    cfg = {"dialect": 3, "path": blank_db(tmp_path)}
    assert execute_query(VALID_SQL, cfg)["error"]["type"] == "invalid_config"


@pytest.mark.parametrize("bad", [None, "", "   ", 42, [], True, b"x"])
def test_missing_or_invalid_path_rejected(tmp_path, bad):
    cfg = {"dialect": "sqlite"} if bad is None else {"dialect": "sqlite", "path": bad}
    assert execute_query(VALID_SQL, cfg)["error"]["type"] == "invalid_config"


def test_directory_path_is_rejected(tmp_path):
    cfg = {"dialect": "sqlite", "path": str(tmp_path)}
    assert execute_query(VALID_SQL, cfg)["error"]["type"] == "invalid_config"


def test_null_byte_in_path_is_rejected(tmp_path):
    cfg = {"dialect": "sqlite", "path": "\x00"}
    assert execute_query(VALID_SQL, cfg)["error"]["type"] == "invalid_config"


def test_oversized_path_is_rejected(tmp_path):
    cfg = {"dialect": "sqlite", "path": "a" * 5000}
    assert execute_query(VALID_SQL, cfg)["error"]["type"] == "invalid_config"


@pytest.mark.parametrize("bad", [None, "x", 5, [], {}])
def test_non_object_config_rejected(bad):
    assert execute_query(VALID_SQL, bad)["error"]["type"] == "invalid_config"


# --- database_config: bounded options -----------------------------------------

@pytest.mark.parametrize("key", ["timeout_s", "max_rows"])
@pytest.mark.parametrize("bad", [0, -1, "5", True, [], {}])
def test_invalid_option_values_rejected(tmp_path, key, bad):
    cfg = {"path": blank_db(tmp_path), key: bad}
    assert execute_query(VALID_SQL, cfg)["error"]["type"] == "invalid_config"


@pytest.mark.parametrize("key", ["timeout_s", "max_rows"])
def test_out_of_range_options_rejected(tmp_path, key):
    for value in (10 ** 9, -5):
        cfg = {"path": blank_db(tmp_path), key: value}
        assert execute_query(VALID_SQL, cfg)["error"]["type"] == "invalid_config"


@pytest.mark.parametrize("key", ["timeout_s", "max_rows"])
def test_non_finite_floats_rejected(tmp_path, key):
    for value in (float("nan"), float("inf")):
        cfg = {"path": blank_db(tmp_path), key: value}
        assert execute_query(VALID_SQL, cfg)["error"]["type"] == "invalid_config"


@pytest.mark.parametrize("key,good", [("timeout_s", DEFAULT_TIMEOUT_S),
                                      ("timeout_s", 12.5),
                                      ("max_rows", DEFAULT_MAX_ROWS),
                                      ("max_rows", 1)])
def test_in_range_options_accepted(tmp_path, key, good):
    path, _ = sample_db(tmp_path)
    cfg = {"path": path, key: good}
    assert execute_query("SELECT 1", cfg)["error"] is None


def test_fractional_max_rows_rejected(tmp_path):
    cfg = {"path": blank_db(tmp_path), "max_rows": 10.5}
    assert execute_query(VALID_SQL, cfg)["error"]["type"] == "invalid_config"


# --- query failure mapping and leakage ---------------------------------------

def test_unknown_table_maps_to_invalid_sql(tmp_path):
    path, _ = sample_db(tmp_path)
    result = execute_query("SELECT * FROM invoices", config(tmp_path, path=path))
    assert result["error"]["type"] == "invalid_sql"
    assert "unknown_relation" in result["error"]["message"]


def test_unknown_column_maps_to_invalid_sql(tmp_path):
    path, _ = sample_db(tmp_path)
    result = execute_query("SELECT salary FROM customers", config(tmp_path, path=path))
    assert result["error"]["type"] == "invalid_sql"
    assert "unknown_column" in result["error"]["message"]


def test_write_smuggled_into_cte_is_rejected(tmp_path):
    path, original = sample_db(tmp_path)
    sql = "WITH d AS (DELETE FROM customers RETURNING *) SELECT * FROM d"
    assert execute_query(sql, config(tmp_path, path=path))["error"]["type"] == "invalid_sql"
    assert open(path, "rb").read() == original


def test_config_is_checked_before_the_query_runs(tmp_path):
    result = execute_query("INSERT INTO t VALUES (1)", {"dialect": "sqlite"})
    assert result["error"]["type"] == "invalid_config"


def test_error_messages_never_leak_path_or_credentials(tmp_path):
    path, _ = sample_db(tmp_path, rows=3)
    cfg = {"path": path, "password": "hunter2", "timeout_s": 0.1}
    cases = [VALID_SQL, "SELECT * FROM ghosts", "DROP TABLE customers",
             "SELCT 1", "", INFINITE_COUNT, "SELECT salary FROM customers"]

    # Both sides must be normalised the same way: repr() doubles backslashes, so a
    # raw Windows path could never match the repr'd payload, and casefolding the
    # payload alone would hide an uppercase drive letter.
    needles = [path.lower(), repr(path)[1:-1].lower(), "payroll", "hunter2"]
    leaked = repr({"error": {"message": f"could not open {path}"}}).lower()
    assert any(needle in leaked for needle in needles), "needles cannot detect a leak"

    for sql in cases:
        blob = repr(execute_query(sql, cfg)).lower()
        for needle in needles:
            assert needle not in blob
        assert "traceback" not in blob
        assert "readonly database" not in blob
        assert "file is not a database" not in blob
        assert "no such" not in blob


def test_error_messages_are_bounded(tmp_path):
    path, _ = sample_db(tmp_path)
    result = execute_query("SELECT " + "x" * 30000, config(tmp_path, path=path))
    assert len(result["error"]["message"]) <= 200
