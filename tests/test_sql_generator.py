"""Mocked tests for backend.sql_generator — no live model needed."""
import pytest

from backend import llm_client, sql_generator
from backend.sql_generator import (
    find_unknown_identifiers, format_schema_context, generate_sql, schema_name_index,
)

SCHEMA = {
    "tables": [
        {"name": "customers", "columns": [
            {"name": "id", "type": "INTEGER", "pk": True},
            {"name": "name", "type": "TEXT"},
            {"name": "region", "type": "TEXT"}]},
        {"name": "orders", "columns": [
            {"name": "id", "type": "INTEGER", "pk": True},
            {"name": "customer_id", "type": "INTEGER", "fk_to": "customers.id"},
            {"name": "order_date", "type": "DATE"},
            {"name": "total", "type": "REAL"}]},
        {"name": "products", "columns": [
            {"name": "id", "type": "INTEGER", "pk": True},
            {"name": "name", "type": "TEXT"},
            {"name": "category", "type": "TEXT"}]},
        {"name": "order_items", "columns": [
            {"name": "id", "type": "INTEGER", "pk": True},
            {"name": "order_id", "type": "INTEGER", "fk_to": "orders.id"},
            {"name": "product_id", "type": "INTEGER", "fk_to": "products.id"},
            {"name": "quantity", "type": "INTEGER"}]},
    ],
    "warnings": ["region has 12% missing values"],
}


@pytest.fixture
def mock_llm(monkeypatch):
    """Patch complete_json at its use site; record prompt messages."""
    calls = []

    def install(payload=None, exc=None):
        def fake(messages, max_tokens=None):
            calls.append(messages)
            if exc:
                raise exc
            return payload
        monkeypatch.setattr(sql_generator.llm_client, "complete_json", fake)
        return calls
    return install


def test_schema_index_collects_names():
    idx = schema_name_index(SCHEMA)
    assert "orders" in idx["tables"] and "customer_id" in idx["columns"]


def test_schema_context_includes_fk_pk_and_warnings():
    ctx = format_schema_context(SCHEMA)
    assert "TABLE orders" in ctx and "FK->customers.id" in ctx and "PK" in ctx
    assert "WARNING: region has 12% missing values" in ctx


def test_schema_context_truncated_to_bound():
    big = {"tables": [{"name": f"t{i}", "columns": [{"name": f"c{j}", "type": "INT"}
                  for j in range(200)]} for i in range(100)]}
    ctx = format_schema_context(big)
    assert len(ctx) <= sql_generator.MAX_SCHEMA_CONTEXT_CHARS + 30
    assert "truncated" in ctx


def test_valid_generation_returns_ok(mock_llm):
    calls = mock_llm(payload={"status": "ok",
                              "sql": "SELECT region, SUM(total) AS s FROM orders "
                                     "JOIN customers ON orders.customer_id = customers.id "
                                     "GROUP BY region",
                              "assumptions": ["total is revenue"]})
    out = generate_sql("revenue by region?", SCHEMA)
    assert out["status"] == "ok" and out["error"] is None
    assert out["assumptions"] == ["total is revenue"]
    assert "TABLE orders" in calls[0][1]["content"]


def test_join_with_aliases_passes_guard(mock_llm):
    mock_llm(payload={"status": "ok",
                      "sql": "SELECT p.category, SUM(oi.quantity) AS total_qty "
                             "FROM order_items oi JOIN products p ON oi.product_id = p.id "
                             "GROUP BY p.category ORDER BY total_qty DESC LIMIT 5",
                      "assumptions": []})
    out = generate_sql("top categories by units?", SCHEMA)
    assert out["status"] == "ok", out


def test_invented_column_is_flagged(mock_llm):
    mock_llm(payload={"status": "ok",
                      "sql": "SELECT revenue FROM orders",
                      "assumptions": []})
    out = generate_sql("total revenue?", SCHEMA)
    assert out["status"] == "error" and out["error"]["type"] == "invented_identifier"
    assert "revenue" in out["error"]["message"]


def test_string_literals_do_not_count_as_identifiers(mock_llm):
    mock_llm(payload={"status": "ok",
                      "sql": "SELECT name FROM customers WHERE region = 'West'",
                      "assumptions": []})
    assert generate_sql("customers in the west?", SCHEMA)["status"] == "ok"


def test_clarification_passthrough(mock_llm):
    mock_llm(payload={"status": "clarification_needed", "sql": None,
                      "assumptions": [], "clarification_question": "Which year?"})
    out = generate_sql("sales by region?", SCHEMA)
    assert out["status"] == "clarification_needed"
    assert out["clarification_question"] == "Which year?"


def test_clarification_without_question_is_error(mock_llm):
    mock_llm(payload={"status": "clarification_needed", "sql": None})
    out = generate_sql("sales?", SCHEMA)
    assert out["status"] == "error" and out["error"]["type"] == "malformed"


def test_unknown_status_is_error(mock_llm):
    mock_llm(payload={"status": "weird"})
    out = generate_sql("q", SCHEMA)
    assert out["status"] == "error" and out["error"]["type"] == "malformed"


def test_ok_status_without_sql_is_error(mock_llm):
    mock_llm(payload={"status": "ok", "sql": "  "})
    out = generate_sql("q", SCHEMA)
    assert out["status"] == "error" and "no SQL" in out["error"]["message"]


def test_llm_offline_returns_error_dict(mock_llm):
    mock_llm(exc=llm_client.LLMError("offline", "server unreachable"))
    out = generate_sql("q", SCHEMA)
    assert out["status"] == "error" and out["error"]["type"] == "offline"


def test_llm_timeout_returns_error_dict(mock_llm):
    mock_llm(exc=llm_client.LLMError("timeout", "too slow"))
    assert generate_sql("q", SCHEMA)["error"]["type"] == "timeout"


def test_repair_attempt_includes_prior_error_feedback(mock_llm):
    calls = mock_llm(payload={"status": "ok", "sql": "SELECT total FROM orders"})
    generate_sql("total sales?", SCHEMA,
                 prior_error={"type": "syntax", "message": "unexpected token"})
    assert "unexpected token" in calls[0][1]["content"]


def test_generate_never_executes_sql(mock_llm, monkeypatch):
    """Contract guard: module must not import or call any DB driver."""
    import importlib
    src = importlib.import_module("backend.sql_generator").__file__
    text = open(src, encoding="utf-8").read()
    for forbidden in ("sqlite3", "execute(", "requests.post", "query_executor"):
        assert forbidden not in text, forbidden
