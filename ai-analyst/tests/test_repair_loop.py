"""Mocked tests for backend.repair_loop — bounded repair, single validator path."""
import pytest

from backend import repair_loop
from backend.repair_loop import generate_validated_sql

SCHEMA = {"tables": [{"name": "orders", "columns": [{"name": "total", "type": "REAL"}]}],
          "dialect": "sqlite"}


def gen(status="ok", sql="SELECT total FROM orders", error=None, question=None):
    return {"status": status, "sql": sql, "assumptions": [],
            "clarification_question": question, "error": error}


class SpyGenerate:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, question, schema, prior_error=None):
        self.calls.append(prior_error)
        return self.results.pop(0)


class SpyValidator:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.seen_sql = []

    def __call__(self, sql, dialect):
        self.seen_sql.append((sql, dialect))
        return self.verdicts.pop(0)


VALID = {"valid": True, "error": None}
def invalid(msg): return {"valid": False, "error": {"type": "syntax", "message": msg}}


def test_first_try_valid_stops_immediately():
    g, v = SpyGenerate([gen()]), SpyValidator([VALID])
    out = generate_validated_sql("q", SCHEMA, v, generate=g)
    assert out["status"] == "ok" and out["validated"] and out["attempts"] == 1
    assert len(g.calls) == 1 and len(v.seen_sql) == 1


def test_repair_feeds_validator_error_into_next_generation():
    g = SpyGenerate([gen(sql="SELECT bad"), gen(sql="SELECT total FROM orders")])
    v = SpyValidator([invalid("unexpected token bad"), VALID])
    out = generate_validated_sql("q", SCHEMA, v, generate=g)
    assert out["status"] == "ok" and out["attempts"] == 2
    assert g.calls[1]["message"] == "unexpected token bad"  # prior_error passed
    assert v.seen_sql[0][0] == "SELECT bad"  # the bad query still hit the validator


def test_every_candidate_passes_same_validator():
    g = SpyGenerate([gen(sql="SELECT a"), gen(sql="SELECT b"), gen(sql="SELECT c")])
    v = SpyValidator([invalid("a"), invalid("b"), VALID])
    out = generate_validated_sql("q", SCHEMA, v, generate=g)
    assert out["status"] == "ok" and out["attempts"] == 3
    assert [sql for sql, _ in v.seen_sql] == ["SELECT a", "SELECT b", "SELECT c"]
    assert all(dialect == "sqlite" for _, dialect in v.seen_sql)


def test_repair_capped_at_two_then_error():
    g = SpyGenerate([gen(sql="SELECT 1"), gen(sql="SELECT 2"), gen(sql="SELECT 3")])
    v = SpyValidator([invalid("e1"), invalid("e2"), invalid("e3")])
    out = generate_validated_sql("q", SCHEMA, v, generate=g)
    assert out["status"] == "error" and not out["validated"]
    assert out["error"]["type"] == "repair_exhausted"
    assert out["error"]["last_validator_error"]["message"] == "e3"  # the actual last error
    assert len(g.calls) == 3 and len(v.seen_sql) == 3  # 1 + 2 repairs, never a 4th


def test_clarification_short_circuits_without_validation():
    g = SpyGenerate([gen(status="clarification_needed", sql=None, question="Which year?")])
    v = SpyValidator([VALID])
    out = generate_validated_sql("q", SCHEMA, v, generate=g)
    assert out["status"] == "clarification_needed"
    assert out["clarification_question"] == "Which year?"
    assert v.seen_sql == []  # nothing to validate


def test_generation_error_short_circuits_without_validation():
    g = SpyGenerate([gen(status="error", sql=None,
                         error={"type": "offline", "message": "down"})])
    v = SpyValidator([VALID])
    out = generate_validated_sql("q", SCHEMA, v, generate=g)
    assert out["status"] == "error" and out["error"]["type"] == "offline"
    assert v.seen_sql == []


def test_loop_never_executes_sql():
    import importlib
    src = importlib.import_module("backend.repair_loop").__file__
    text = open(src, encoding="utf-8").read()
    for forbidden in ("sqlite3", "execute(", "requests.post", "query_executor"):
        assert forbidden not in text, forbidden


def test_default_generate_is_sql_generator(monkeypatch):
    monkeypatch.setattr(repair_loop.sql_generator, "generate_sql",
                        lambda q, s, prior_error=None: gen())
    v = SpyValidator([VALID])
    out = generate_validated_sql("q", SCHEMA, v)
    assert out["status"] == "ok"


# ---- P2(a): empty completions are transiently retryable (live evidence) ----

def test_empty_error_is_retried_then_succeeds():
    g = SpyGenerate([gen(status="error", sql=None,
                         error={"type": "empty", "message": "LLM returned an empty completion"}),
                     gen(sql="SELECT total FROM orders")])
    v = SpyValidator([VALID])
    out = generate_validated_sql("q", SCHEMA, v, generate=g)
    assert out["status"] == "ok" and out["attempts"] == 2
    assert g.calls[1]["type"] == "empty"  # retry feedback carries the cause


def test_empty_retry_shares_budget_with_repairs():
    # empty, validator reject, empty again -> 4th attempt never happens
    g = SpyGenerate([
        gen(status="error", sql=None, error={"type": "empty", "message": "empty"}),
        gen(sql="SELECT bad FROM orders"),
        gen(status="error", sql=None, error={"type": "empty", "message": "empty"}),
        gen(sql="SELECT never = reached"),
    ])
    v = SpyValidator([invalid("bad column")])
    out = generate_validated_sql("q", SCHEMA, v, generate=g)
    assert out["status"] == "error" and len(g.calls) == 3
    assert out["error"]["type"] == "empty"  # last real cause survives


def test_timeout_still_fails_fast():
    g = SpyGenerate([gen(status="error", sql=None,
                         error={"type": "timeout", "message": "exceeded"})])
    v = SpyValidator([])
    out = generate_validated_sql("q", SCHEMA, v, generate=g)
    assert out["status"] == "error" and out["error"]["type"] == "timeout"
    assert len(g.calls) == 1 and v.seen_sql == []
