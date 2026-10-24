"""Tests for backend.result_verifier — envelope verification, isolated.

Covers the verdict itself: consistent envelopes pass, empty results pass as
successes, failed executions never pass, and malformed or self-contradictory
envelopes are refused. The last group runs against real `execute_query` output to
prove the shapes match, without the verifier being wired into the executor.
"""
import sqlite3

import pytest

from backend.query_executor import ENVELOPE_KEYS, execute_query
from backend.result_verifier import VERIFIER_KEYS, VERIFIER_SCOPE, verify_result


def envelope(**overrides):
    base = {"status": "success", "sql": "SELECT region, total FROM customers",
            "dialect": "sqlite", "columns": ["region", "total"],
            "rows": [["east", 10], ["west", 20]], "row_count": 2,
            "truncated": False, "warnings": [], "elapsed_ms": 4.2, "error": None}
    base.update(overrides)
    return base


def verify(**overrides):
    return verify_result("total by region?", envelope(**overrides))


# --- shape of the verdict -----------------------------------------------------

def test_verdict_always_carries_every_key():
    assert list(verify()) == list(VERIFIER_KEYS)
    assert list(verify_result("q", None)) == list(VERIFIER_KEYS)
    assert list(verify_result("q", envelope(row_count=99))) == list(VERIFIER_KEYS)


@pytest.mark.parametrize("bad", [None, "result", 7, [], [{"status": "success"}]])
def test_non_dict_result_is_refused_not_raised(bad):
    out = verify_result("q", bad)
    assert out["passed"] is False
    assert out["status"] == "error"
    assert out["error"]["type"] == "invalid_input"


def test_an_empty_dict_is_a_missing_field_problem_not_a_type_problem():
    out = verify_result("q", {})
    assert out["passed"] is False
    assert out["error"]["type"] == "missing_result_fields"


@pytest.mark.parametrize("bad", [None, 7, [], b"q"])
def test_non_string_question_is_refused_not_raised(bad):
    out = verify_result(bad, envelope())
    assert out["passed"] is False
    assert out["error"]["type"] == "invalid_input"


def test_verdict_never_mutates_the_result():
    result = envelope()
    snapshot = {k: (list(v) if isinstance(v, list) else v) for k, v in result.items()}
    verify_result("q", result)
    assert result == snapshot


def test_scope_declares_what_was_established():
    assert verify()["scope"] == VERIFIER_SCOPE


def test_passing_explanation_does_not_claim_factual_correctness():
    out = verify()
    assert out["passed"] is True
    assert "not that the data answers the question" in out["explanation"]
    assert "correct" not in out["explanation"].lower()


# --- a consistent envelope passes --------------------------------------------

def test_consistent_envelope_passes():
    out = verify()
    assert out["passed"] is True
    assert out["status"] == "success"
    assert out["error"] is None
    assert out["failures"] == []
    assert out["evidence"]["row_count"] == 2
    assert out["evidence"]["columns"] == ["region", "total"]
    assert "row_count_matches_rows" in out["checks_passed"]
    assert "execution_succeeded" in out["checks_passed"]


def test_single_value_per_column_is_required():
    out = verify(columns=["region", "total"], rows=[["east", 10, "extra"]], row_count=1)
    assert out["passed"] is False
    assert any(f["reason"] == "ragged_rows" for f in out["failures"])


def test_rows_without_columns_fail():
    out = verify(columns=[], rows=[["east"]], row_count=1)
    assert out["passed"] is False
    assert any(f["reason"] == "columns_missing_for_rows" for f in out["failures"])


def test_sql_must_be_reported_on_success():
    for bad in (None, "", "   ", 7):
        out = verify(sql=bad)
        assert out["passed"] is False
        assert any(f["check"] == "sql_present" for f in out["failures"])


# --- empty results ------------------------------------------------------------

def test_empty_result_is_a_verified_success():
    out = verify(columns=["region"], rows=[], row_count=0)
    assert out["passed"] is True
    assert out["evidence"]["empty_result"] is True
    assert any("empty" in w.casefold() for w in out["warnings"])
    assert any("not an error" in w for w in out["warnings"])


def test_empty_rows_with_nonzero_count_is_inconsistent():
    out = verify(rows=[], row_count=3)
    assert out["passed"] is False
    assert any(f["reason"] == "row_count_mismatch" for f in out["failures"])


# --- failed execution is never verified --------------------------------------

def test_failed_execution_is_not_verified():
    out = verify(status="error", rows=[], row_count=0, columns=[],
                 error={"type": "permission_denied",
                        "message": "the query requires write access; execution is read-only"})
    assert out["passed"] is False
    assert out["status"] == "success"  # the verifier ran; the verdict is negative
    failure = out["failures"][0]
    assert failure["check"] == "execution_succeeded"
    assert failure["reason"] == "permission_denied"
    assert "never be marked verified" in failure["message"]


@pytest.mark.parametrize("type_", ["timeout", "database_not_found", "invalid_sql",
                                   "database_unavailable", "execution_failed"])
def test_every_executor_failure_type_is_refused(type_):
    out = verify(status="error", rows=[], row_count=0, columns=[],
                 error={"type": type_, "message": "controlled message"})
    assert out["passed"] is False
    assert out["failures"][0]["reason"] == type_


def test_tidy_but_failed_result_still_fails():
    """Internal tidiness must not promote a failed execution."""
    out = verify(status="error", columns=[], rows=[], row_count=0, truncated=False,
                 warnings=[], error={"type": "timeout", "message": "took too long"})
    assert out["passed"] is False


def test_error_object_on_a_success_is_inconsistent():
    out = verify(error={"type": "execution_failed", "message": "boom"})
    assert out["passed"] is False
    assert any(f["reason"] == "error_present_on_success" for f in out["failures"])


def test_malformed_error_object_is_reported():
    out = verify(status="error", rows=[], row_count=0,
                 error="unable to open database file")
    assert out["passed"] is False
    assert any(f["reason"] == "error_object_malformed" for f in out["failures"])


def test_clarification_needed_from_an_executor_is_not_verified():
    out = verify(status="clarification_needed")
    assert out["passed"] is False


def test_unknown_status_is_flagged():
    out = verify(status="ok")
    assert out["passed"] is False
    reasons = [f["reason"] for f in out["failures"]]
    assert "unexpected_status" in reasons


# --- missing required fields --------------------------------------------------

@pytest.mark.parametrize("key", ENVELOPE_KEYS)
def test_dropping_any_required_field_is_refused(key):
    result = envelope()
    del result[key]
    out = verify_result("q", result)
    assert out["passed"] is False
    assert out["status"] == "error"
    assert out["error"]["type"] == "missing_result_fields"
    assert key in out["error"]["message"]


# --- truncation ---------------------------------------------------------------

def test_truncated_with_warning_passes_and_warns():
    out = verify(columns=["region"], truncated=True, rows=[["east"]], row_count=1,
                 warnings=["Results were truncated to the first 1 rows; more matching "
                           "rows exist but were not read."])
    assert out["passed"] is True
    assert any("true number of matching rows is unknown" in w for w in out["warnings"])


def test_truncated_without_a_warning_fails():
    out = verify(truncated=True, warnings=[])
    assert out["passed"] is False
    assert any(f["reason"] == "truncation_warning_missing" for f in out["failures"])


def test_warning_claiming_truncation_without_the_flag_fails():
    out = verify(truncated=False,
                 warnings=["Results were truncated to the first 1000 rows"])
    assert out["passed"] is False
    assert any(f["reason"] == "truncation_warning_without_truncation"
               for f in out["failures"])


def test_truncated_result_cannot_be_empty():
    out = verify(truncated=True, rows=[], row_count=0, columns=[],
                 warnings=["Results were truncated to the first 0 rows"])
    assert out["passed"] is False
    assert any(f["reason"] == "truncated_but_empty" for f in out["failures"])


# --- field type integrity -----------------------------------------------------

@pytest.mark.parametrize("bad", [-1, "2", 2.0, True, None, [2]])
def test_bad_row_count_values_are_rejected(bad):
    out = verify(row_count=bad)
    assert out["passed"] is False
    assert any(f["reason"] == "row_count_malformed" for f in out["failures"])


@pytest.mark.parametrize("bad", [None, "x", float("nan"), float("inf"), -1, True])
def test_bad_elapsed_values_are_rejected(bad):
    out = verify(elapsed_ms=bad)
    assert out["passed"] is False
    assert any(f["reason"] == "elapsed_ms_malformed" for f in out["failures"])


@pytest.mark.parametrize("bad", [None, "cols", [1, 2], {"a": 1}])
def test_bad_columns_are_rejected(bad):
    out = verify(columns=bad, rows=[], row_count=0)
    assert out["passed"] is False


@pytest.mark.parametrize("bad", [None, "warn", [7]])
def test_bad_warnings_are_rejected(bad):
    out = verify(warnings=bad)
    assert out["passed"] is False


@pytest.mark.parametrize("bad", [None, "yes", 1])
def test_truncated_must_be_boolean(bad):
    out = verify(truncated=bad)
    assert out["passed"] is False
    assert any(f["reason"] == "truncated_not_boolean" for f in out["failures"])


def test_rows_must_be_sequences():
    out = verify(rows={"east": 10})
    assert out["passed"] is False
    assert any(f["reason"] == "rows_malformed" for f in out["failures"])


# --- already-verified input ---------------------------------------------------

def test_reverifying_a_passed_result_is_stable():
    first = verify()
    assert first["passed"] is True
    second = verify(verification={"passed": True})
    assert second["passed"] is True


def test_result_carrying_a_failed_verification_stays_failed():
    out = verify(verification={"passed": False})
    assert out["passed"] is False
    assert any(f["reason"] == "previous_verification_failed" for f in out["failures"])


def test_malformed_verification_field_is_reported():
    out = verify(verification="yes")
    assert out["passed"] is False
    assert any(f["reason"] == "verification_object_malformed" for f in out["failures"])


# --- no leakage ---------------------------------------------------------------

def test_verdict_never_quotes_values_paths_or_the_question():
    question = "show rows from C:\\temp\\payroll.db"
    result = envelope(columns=["ssn"], rows=[["123-45-6789"]], row_count=1,
                      sql="SELECT ssn FROM payroll")
    out = verify_result(question, result)
    blob = repr(out).lower()
    # Cell values and paths never appear; column names do, as structural metadata.
    for secret in ("123-45-6789", "payroll.db", "c:\\temp"):
        assert secret not in blob

    # Same on a failing verdict, where messages are actually produced.
    broken = envelope(row_count=7, truncated="yes", warnings=["truncated to 1000 rows"])
    failure_verdict = verify_result(question, broken)
    assert failure_verdict["passed"] is False
    fail_blob = repr(failure_verdict).lower()
    assert "123-45-6789" not in fail_blob
    assert "payroll" not in fail_blob
    assert "traceback" not in fail_blob
    for failure in failure_verdict["failures"]:
        assert len(failure["message"]) <= 200
        assert "123-45-6789" not in failure["message"]


# --- against real executor output --------------------------------------------

def make_db(tmp_path, rows=3, name="verify.db"):
    path = tmp_path / name
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, region TEXT)")
    conn.executemany("INSERT INTO customers (id, region) VALUES (?, ?)",
                     [(i, f"region{i % 2}") for i in range(1, rows + 1)])
    conn.commit()
    conn.close()
    return str(path)


def test_real_executor_result_verifies(tmp_path):
    path = make_db(tmp_path, rows=3)
    result = execute_query("SELECT id, region FROM customers ORDER BY id", {"path": path})
    out = verify_result("how many customers per region?", result)
    assert result["status"] == "success"
    assert out["passed"] is True
    assert out["evidence"]["row_count"] == 3


def test_real_executor_empty_result_verifies(tmp_path):
    path = make_db(tmp_path, rows=2)
    result = execute_query("SELECT id FROM customers WHERE region = 'nowhere'",
                           {"path": path})
    out = verify_result("customers in nowhere?", result)
    assert result["row_count"] == 0
    assert out["passed"] is True
    assert out["evidence"]["empty_result"] is True


def test_real_executor_truncated_result_verifies(tmp_path):
    path = make_db(tmp_path, rows=10)
    result = execute_query("SELECT id FROM customers ORDER BY id",
                           {"path": path, "max_rows": 3})
    out = verify_result("list customers?", result)
    assert result["truncated"] is True
    assert out["passed"] is True


def test_real_executor_failure_is_not_verified(tmp_path):
    result = execute_query("SELECT 1", {"path": "/no/such/file.db"})
    out = verify_result("anything?", result)
    assert result["status"] == "error"
    assert out["passed"] is False
    assert out["failures"][0]["reason"] == result["error"]["type"]


def test_real_executor_rejected_write_is_not_verified(tmp_path):
    path = make_db(tmp_path, rows=2)
    result = execute_query("DROP TABLE customers", {"path": path})
    out = verify_result("drop the table?", result)
    assert out["passed"] is False
    assert out["failures"][0]["reason"] == "invalid_sql"
