"""Gateway envelope regressions: structural verification scope must reach the
UI honestly, and the new Agent 3 type-guard error must map into the contract."""
from gateway.orchestrator import _canonical_error, _error_code, _verification_for

VERDICT = {"passed": True, "scope": "envelope_consistency",
          "checks_passed": ["field_types"], "failures": [], "warnings": [],
          "explanation": "Result envelope is internally consistent.",
          "evidence": {}}


def test_verification_envelope_carries_structural_scope():
    out = _verification_for({"status": "ok"}, {}, VERDICT)
    assert out["passed"] is True
    assert out["scope"] == "envelope_consistency"
    assert "structure only" not in out["message"]  # message stays Agent 3's own text


def test_type_unsafe_aggregate_maps_to_contract_error():
    err = _canonical_error("type_unsafe_aggregate", "SUM(events.amount) refused")
    assert err["type"] == "type_unsafe_aggregate"
    assert err["detail"] == "type_unsafe_aggregate"
    assert _error_code(err) == "unsafe_sql"


def test_generator_join_errors_map_to_unsafe_sql():
    for detail in ("unvalidated_join", "unknown_qualified_column"):
        assert _error_code(_canonical_error(detail, "x")) == "unsafe_sql"
