"""Bounded repair loop (Agent 1, Part 7).

generate_validated_sql() drives: generate → validate → (repair ≤2 times) → validate.
The SQL validator is INJECTED (Agent 3's validate_sql) — this module never touches a
database. Every candidate, initial or repaired, passes through the SAME validator
callable (report §5 single-execution-path rule). Total generation attempts ≤ 3
(1 initial + 2 repairs).

Agent 4's orchestrator may drive this loop itself; this helper implements the
contracted semantics so both owners get identical behavior.
"""
from __future__ import annotations

from backend import sql_generator

MAX_REPAIR_ATTEMPTS = 2
REPAIRABLE_ERROR_TYPES = frozenset({"invented_identifier", "empty",
                                    "unknown_qualified_column", "unvalidated_join"})


def generate_validated_sql(question: str, schema: dict, validate_sql,
                           generate=None, max_repairs: int = MAX_REPAIR_ATTEMPTS) -> dict:
    """generate: callable(question, schema, prior_error) -> contract dict.
    validate_sql: callable(sql, dialect) -> {"valid": bool, "error": {...}|None}.
    Returns {"status": "ok"|"clarification_needed"|"error", "sql", "attempts",
            "assumptions", "clarification_question", "error", "validated": bool}.
    """
    generate = generate or sql_generator.generate_sql
    prior_error = None
    last_validation_error = None
    last_generation = None

    for attempt in range(1, max_repairs + 2):  # 1 initial + up to max_repairs
        last_generation = generate(question, schema, prior_error=prior_error)
        status = last_generation.get("status")

        if status == "clarification_needed":
            return _final(last_generation, attempt, validated=False, status="clarification_needed")

        if status != "ok":
            # Repairable SQL errors (INTEGRATION_CONTRACT.md §5.3): invented identifiers
            # and intermittent empty completions (live evidence, RESULTS.md finding 1).
            # Transport/malformed failures are not repairable.
            error = last_generation.get("error") or {}
            if (error.get("type") in REPAIRABLE_ERROR_TYPES and attempt <= max_repairs):
                prior_error = error
                continue
            return _final(last_generation, attempt, validated=False, status="error")

        verdict = validate_sql(last_generation["sql"], schema.get("dialect", "sqlite"))
        if verdict.get("valid"):
            return _final(last_generation, attempt, validated=True, status="ok")

        last_validation_error = verdict.get("error") or {
            "type": "invalid_sql", "message": "validator rejected the query"}
        if attempt > max_repairs:
            break
        prior_error = last_validation_error

    out = _final(last_generation, max_repairs + 1, validated=False, status="error")
    out["error"] = {"type": "repair_exhausted",
                    "message": f"SQL still invalid after {max_repairs} repair attempts",
                    "last_validator_error": last_validation_error}
    return out


def _final(generation: dict, attempts: int, validated: bool, status: str) -> dict:
    return {
        "status": status,
        "sql": generation.get("sql"),
        "attempts": attempts,
        "assumptions": generation.get("assumptions", []),
        "clarification_question": generation.get("clarification_question"),
        "error": generation.get("error"),
        "validated": validated,
    }
