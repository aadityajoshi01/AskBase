"""Part 8 coverage tests: remaining report §3 checklist items + one composed path."""
import pytest

from backend import repair_loop, sql_generator
from backend.agent import synthesize_answer
from backend.llm_client import LLMError


# ---- invalid schema ----

def test_empty_schema_flags_all_identifiers_as_invented(monkeypatch):
    monkeypatch.setattr(sql_generator.llm_client, "complete_json",
                        lambda m, max_tokens=None: {"status": "ok",
                                                    "sql": "SELECT amount FROM invoices",
                                                    "assumptions": []})
    out = sql_generator.generate_sql("total amount?", {"tables": []})
    assert out["status"] == "error" and out["error"]["type"] == "invented_identifier"


def test_schema_missing_optional_keys_is_tolerated(monkeypatch):
    monkeypatch.setattr(sql_generator.llm_client, "complete_json",
                        lambda m, max_tokens=None: {"status": "ok",
                                                    "sql": "SELECT name FROM users",
                                                    "assumptions": []})
    # no 'warnings', columns lack 'type'
    schema = {"tables": [{"name": "users", "columns": [{"name": "name"}]}]}
    out = sql_generator.generate_sql("list names?", schema)
    assert out["status"] == "ok"


# ---- insufficient evidence ----

def test_verification_mismatch_refused_even_with_rows(monkeypatch):
    """Rows exist but verification says the reported figure is wrong → refuse."""
    monkeypatch.setattr("backend.agent.llm_client.chat_completion",
                        lambda m, max_tokens=None: pytest.fail("must not call model"))
    result = {"status": "success", "columns": ["total"], "rows": [[999]],
              "row_count": 1, "truncated": False, "warnings": [],
              "verification": {"passed": False,
                               "reason": "stated total does not match returned rows"}}
    out = synthesize_answer("total spend?", "SELECT SUM(total) ...", result)
    assert out["status"] == "error"
    assert "No answer" in out["answer"]


# ---- composed path: real generator + injected validator, mocked LLM ----

def test_repair_loop_with_real_generator_and_valid_sql(monkeypatch):
    payloads = [
        {"status": "ok", "sql": "SELECT revenue FROM orders", "assumptions": []},  # invented col
        {"status": "ok", "sql": "SELECT total FROM orders", "assumptions": []},    # repaired
    ]
    monkeypatch.setattr(sql_generator.llm_client, "complete_json",
                        lambda m, max_tokens=None: payloads.pop(0))
    seen = []

    def validator(sql, dialect):
        seen.append(sql)
        return {"valid": True, "error": None}

    schema = {"tables": [{"name": "orders",
                          "columns": [{"name": "total", "type": "REAL"}]}],
              "dialect": "sqlite"}
    out = repair_loop.generate_validated_sql("total revenue?", schema, validator)
    assert out["status"] == "ok" and out["attempts"] == 2
    # the invented column never reached the validator; only repaired SQL did
    assert seen == ["SELECT total FROM orders"]


def test_invented_identifier_repair_stops_at_cap(monkeypatch):
    payloads = [{"status": "ok", "sql": f"SELECT revenue{i} FROM orders", "assumptions": []}
                for i in range(5)]
    monkeypatch.setattr(sql_generator.llm_client, "complete_json",
                        lambda m, max_tokens=None: payloads.pop(0))
    calls = []

    def validator(sql, dialect):
        calls.append(sql)
        return {"valid": True, "error": None}

    schema = {"tables": [{"name": "orders",
                          "columns": [{"name": "total", "type": "REAL"}]}]}
    out = repair_loop.generate_validated_sql("revenue?", schema, validator)
    assert out["status"] == "error"
    assert out["error"]["type"] == "invented_identifier"  # last real cause survives
    assert len(payloads) == 2  # exactly 3 generations of 5 queued
    assert calls == []
