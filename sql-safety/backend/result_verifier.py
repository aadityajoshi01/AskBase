"""Result verification (Agent 3, milestone 3 — first version, not yet integrated).

`verify_result(question, result)` checks exactly one thing: whether an
`execute_query` result envelope is **internally consistent and reports a successful
read-only execution**. It is a structural gate, not a correctness gate.

What passing means: the envelope had every required field, execution status was
`success` with no error object, `row_count` matched the rows actually returned,
every row had one value per column, an empty result was distinguished from a
failure, and a truncated result disclosed itself with a matching warning.

What passing does NOT mean — deliberately out of reach for this interface:

- it does not show the query answered the user's question. `question` is used only
  for input validation; there is no SQL-to-question equivalence check, and a
  structural verifier cannot make that claim honestly;
- it does not check reported counts, totals or rankings against the rows. Report §5
  asks for those, but the figures being checked live in Agent 1's `answer` text (and
  in `chart`), and `verify_result(question, result)` receives neither. See
  "Unresolved interface questions" in the README;
- it does not re-run the query, compare it against a second source, or confirm the
  database holds true data.

So a wrong-but-well-formed query passes. Callers must label this as envelope
verification, never as factual verification — and `scope` is returned so a caller can
assert which kind of evidence it holds.

Like the validator and executor, this function never raises: every outcome is a dict
with the same keys (`VERIFIER_KEYS`). Failure messages are fixed, controlled strings
built from field names, counts and reason tokens — never row or column values,
filesystem paths, credentials or raw exception text. The input dict is never mutated.
"""
from __future__ import annotations

import math

from backend.query_executor import ENVELOPE_KEYS
from backend.sql_validator import MAX_MESSAGE_LENGTH

# The verifier's own result keys — always all present, in this order.
VERIFIER_KEYS = (
    "passed", "status", "scope", "checks_passed", "failures",
    "warnings", "evidence", "explanation", "error",
)

VERIFIER_SCOPE = "envelope_consistency"
MAX_EXPLANATION_LENGTH = 400

_STATUS_VOCABULARY = frozenset({"success", "error", "clarification_needed"})
# A truncation disclosure is recognised by this stem rather than by matching the
# executor's exact sentence, so the two modules are not textually coupled.
_TRUNCATION_STEM = "truncat"

_NOT_VERIFIED_ON_FAILURE = ("a failed execution can never be marked verified; the "
                            "answer must be refused, not narrated")


def _verdict(passed: bool, checks: list[str], failures: list[dict],
             warnings: list[str], evidence: dict, explanation: str,
             error: dict | None) -> dict:
    return {
        "passed": passed,
        "status": "error" if error else "success",
        "scope": VERIFIER_SCOPE,
        "checks_passed": list(checks),
        "failures": list(failures),
        "warnings": list(warnings),
        "evidence": dict(evidence),
        "explanation": str(explanation)[:MAX_EXPLANATION_LENGTH],
        "error": error,
    }


def _fail(check: str, reason: str, message: str) -> dict:
    return {"check": check, "reason": reason,
            "message": str(message)[:MAX_MESSAGE_LENGTH]}


def _stop(checks: list[str], error_type: str, message: str) -> dict:
    """The verifier itself could not run; that is never a pass."""
    error = {"type": error_type, "message": str(message)[:MAX_MESSAGE_LENGTH]}
    return _verdict(False, checks, [_fail("input_usable", error_type, message)],
                    [], {"row_count": None, "columns": [], "truncated": None,
                         "empty_result": False, "execution_status": None},
                    f"Verification did not complete: {error['type']}. The result is "
                    f"not verified.", error)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_fields(result: dict, checks: list[str], failures: list[dict],
                  warnings: list[str]) -> None:
    """Type checks on every envelope field; findings are appended, never raised."""
    if result.get("status") not in _STATUS_VOCABULARY:
        failures.append(_fail("status_vocabulary", "unexpected_status",
                              "status is not one of the contract values"))
    else:
        checks.append("status_vocabulary")

    if not isinstance(result.get("truncated"), bool):
        failures.append(_fail("field_types", "truncated_not_boolean",
                              "truncated must be a boolean"))

    columns = result.get("columns")
    if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
        failures.append(_fail("field_types", "columns_malformed",
                              "columns must be a list of strings"))

    rows = result.get("rows")
    if not isinstance(rows, list) or not all(isinstance(r, (list, tuple)) for r in rows):
        failures.append(_fail("field_types", "rows_malformed",
                              "rows must be a list of row sequences"))

    warnings_field = result.get("warnings")
    if not isinstance(warnings_field, list) or not all(isinstance(w, str)
                                                       for w in warnings_field):
        failures.append(_fail("field_types", "warnings_malformed",
                              "warnings must be a list of strings"))

    if not _is_int(result.get("row_count")) or result.get("row_count") < 0:
        failures.append(_fail("field_types", "row_count_malformed",
                              "row_count must be a non-negative integer"))

    elapsed = result.get("elapsed_ms")
    bad_elapsed = (not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool)
                   or (isinstance(elapsed, float) and not math.isfinite(elapsed))
                   or elapsed < 0)
    if bad_elapsed:
        failures.append(_fail("field_types", "elapsed_ms_malformed",
                              "elapsed_ms must be a non-negative finite number"))

    if "dialect" in result and result["dialect"] is not None and \
            not isinstance(result["dialect"], str):
        failures.append(_fail("field_types", "dialect_malformed",
                              "dialect must be a string or null"))

    if not failures:
        checks.append("field_types")


def _check_consistency(result: dict, checks: list[str], failures: list[dict],
                       warnings: list[str]) -> None:
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    columns = result.get("columns") if isinstance(result.get("columns"), list) else []
    row_count = result.get("row_count")
    truncated = result.get("truncated")
    warns = result.get("warnings") if isinstance(result.get("warnings"), list) else []

    # 1. reported count vs actual payload
    if _is_int(row_count) and row_count != len(rows):
        failures.append(_fail("row_count_matches_rows", "row_count_mismatch",
                              f"row_count reported {row_count} rows but {len(rows)} "
                              f"were present"))
    else:
        checks.append("row_count_matches_rows")

    # 2. every row has one value per column (counts only, never values)
    if columns and rows:
        ragged = sum(1 for r in rows if len(r) != len(columns))
        if ragged:
            failures.append(_fail("row_shape_matches_columns", "ragged_rows",
                                  f"{ragged} row(s) do not have one value per column"))
        else:
            checks.append("row_shape_matches_columns")
    elif rows and not columns:
        failures.append(_fail("row_shape_matches_columns", "columns_missing_for_rows",
                              "rows were returned but columns is empty"))
    else:
        checks.append("row_shape_matches_columns")

    # 3. truncation must be disclosed, and never disclosed without truncation
    truncation_warnings = [w for w in warns if isinstance(w, str)
                           and _TRUNCATION_STEM in w.casefold()]
    if truncated is True:
        if not truncation_warnings:
            failures.append(_fail("truncation_disclosed", "truncation_warning_missing",
                                  "truncated is true but no warning discloses the "
                                  "row limit"))
        elif len(rows) == 0:
            failures.append(_fail("truncation_disclosed", "truncated_but_empty",
                                  "a truncated result cannot contain zero rows"))
        else:
            checks.append("truncation_disclosed")
            warnings.append("Result was truncated by the executor's row limit; "
                            "the true number of matching rows is unknown.")
    elif truncated is False:
        if truncation_warnings:
            failures.append(_fail("truncation_disclosed", "truncation_warning_without_"
                                  "truncation", "truncated is false but a warning "
                                  "claims rows were truncated"))
        else:
            checks.append("truncation_disclosed")

    # 4. an empty result is a successful outcome and must not be treated as failure
    if row_count == 0 and not rows:
        checks.append("empty_result_is_valid_success")
        warnings.append("Empty result set: the query executed successfully and found "
                        "no matching rows. This is a verified outcome, not an error.")


def _check_error_object(result: dict, checks: list[str], failures: list[dict]) -> None:
    error = result.get("error")
    if error is None:
        return
    if (isinstance(error, dict) and isinstance(error.get("type"), str)
            and isinstance(error.get("message"), str)):
        checks.append("error_object_shape")
        return
    failures.append(_fail("error_object_shape", "error_object_malformed",
                          "error must be null or an object with string type and "
                          "message"))


def _summarize(failures: list[dict]) -> str:
    reasons = ", ".join(f["reason"] for f in failures) or "none"
    return f"Result envelope failed verification (reasons: {reasons})."


def verify_result(question: str, result: dict) -> dict:
    """Verify one execution result envelope. Never raises, never mutates the input.

    Returns (see `VERIFIER_KEYS`):

        {"passed": bool,                     # the verdict callers should gate on
         "status": "success" | "error",      # did the verifier itself complete
         "scope": "envelope_consistency",    # what was, and was not, established
         "checks_passed": [str],
         "failures": [{"check", "reason", "message"}],
         "warnings": [str],
         "evidence": {"row_count", "columns", "truncated", "empty_result",
                      "execution_status"},
         "explanation": str,
         "error": {"type", "message"} | None}

    Contract note: `INTEGRATION_CONTRACT.md` §3 shows Agent 4 folding this into
    `verification: {"passed": true}`. `passed` is a superset-compatible key, so the
    verdict can be embedded directly — but the contract does not yet say whether a
    *missing* verification counts as passed or failed, and Agent 1 currently treats a
    missing one as acceptable (`agent.py:58-60` refuses only on `passed is False`).
    """
    checks: list[str] = []

    if not isinstance(result, dict):
        return _stop(checks, "invalid_input",
                     "result must be the dict returned by execute_query")
    checks.append("input_is_result_dict")

    if not isinstance(question, str):
        return _stop(checks, "invalid_input", "question must be a string")
    checks.append("input_types")

    missing = [key for key in ENVELOPE_KEYS if key not in result]
    if missing:
        return _stop(checks, "missing_result_fields",
                     f"result is missing required field(s): {', '.join(missing)}")
    checks.append("envelope_complete")

    evidence = {"row_count": result["row_count"] if _is_int(result["row_count"]) else None,
                "columns": list(result["columns"]) if isinstance(result["columns"], list)
                else [],
                "truncated": result["truncated"] if isinstance(result["truncated"], bool)
                else None,
                "empty_result": False,
                "execution_status": result["status"]
                if result["status"] in _STATUS_VOCABULARY else None}

    # A failed execution is never verified — checked before anything else so no
    # amount of internal tidiness can promote it (report §5, contract §5.5).
    failures: list[dict] = []
    warnings: list[str] = []

    prior = result.get("verification")
    if prior is not None and not (isinstance(prior, dict)
                                  and isinstance(prior.get("passed"), bool)):
        failures.append(_fail("prior_verification", "verification_object_malformed",
                              "a verification field is present but is not an object "
                              "with a boolean passed"))
    elif isinstance(prior, dict) and prior.get("passed") is False:
        failures.append(_fail("prior_verification", "previous_verification_failed",
                              "the result already carries a failed verification"))

    if result["status"] != "success":
        reason = (result["error"] or {}).get("type") if isinstance(result.get("error"),
                                                                  dict) else None
        failures.append(_fail("execution_succeeded",
                              reason if isinstance(reason, str) else "execution_not_successful",
                              _NOT_VERIFIED_ON_FAILURE))
        _check_fields(result, checks, failures, warnings)
        _check_error_object(result, checks, failures)
        evidence["empty_result"] = (_is_int(result["row_count"])
                                    and result["row_count"] == 0 and not result["rows"])
        return _verdict(False, checks, failures, warnings, evidence,
                        _summarize(failures), None)

    checks.append("execution_succeeded")

    if not isinstance(result["sql"], str) or not result["sql"].strip():
        failures.append(_fail("sql_present", "sql_missing",
                              "a successful execution must report the SQL it ran"))
    else:
        checks.append("sql_present")

    _check_fields(result, checks, failures, warnings)
    _check_consistency(result, checks, failures, warnings)

    if result.get("error") is not None:
        failures.append(_fail("error_object_shape", "error_present_on_success",
                              "status is success but an error object is present"))
    else:
        checks.append("error_object_shape")

    evidence["empty_result"] = (_is_int(result["row_count"])
                                and result["row_count"] == 0 and not result["rows"])

    if failures:
        return _verdict(False, checks, failures, warnings, evidence,
                        _summarize(failures), None)

    explanation = ("Result envelope is internally consistent and execution succeeded. "
                   "This establishes structure only, not that the data answers the "
                   "question.")
    return _verdict(True, checks, failures, warnings, evidence, explanation, None)
