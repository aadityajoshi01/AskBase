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


def test_system_prompt_teaches_the_sqlite_date_functions():
    """Live-eval finding: on the Superstore set the model repeatedly emitted Postgres
    EXTRACT()/DATE_TRUNC(), spent the whole repair budget on it and returned nothing."""
    prompt = sql_generator.SYSTEM_PROMPT
    assert "SQLite" in prompt
    assert "strftime('%Y', col)" in prompt
    assert "EXTRACT()" in prompt and "DATE_TRUNC()" in prompt
    # the 4096-token server context leaves room for the schema block; keep it that way
    assert len(prompt) <= 2000


def test_wrong_dialect_function_feedback_names_the_replacement(mock_llm):
    """Measured live: three bare "Not in schema: EXTRACT" retries changed nothing."""
    mock_llm(payload={"status": "ok",
                      "sql": "SELECT EXTRACT(MONTH FROM order_date) AS m, SUM(total) "
                             "FROM orders GROUP BY m"})
    out = sql_generator.generate_sql("Which month?", SCHEMA)
    assert out["status"] == "error"
    assert out["error"]["type"] == "invented_identifier"
    assert "Not in schema: EXTRACT" in out["error"]["message"]
    assert "strftime" in out["error"]["message"]


def test_plain_invented_column_gets_no_dialect_hint(mock_llm):
    mock_llm(payload={"status": "ok", "sql": "SELECT margin FROM orders"})
    out = sql_generator.generate_sql("What margin?", SCHEMA)
    assert out["error"]["message"] == "Not in schema: margin"


def test_dialect_sits_next_to_the_question_not_only_in_the_system_prompt(mock_llm):
    """An 8B model ignores a mid-list system rule it has already violated; the dialect
    line is repeated in the user turn, immediately before the model answers."""
    calls = mock_llm(payload={"status": "ok", "sql": "SELECT region FROM customers"})
    sql_generator.generate_sql("Which regions?", SCHEMA)
    user_turn = calls[0][1]["content"]
    assert "SQLite" in user_turn and "strftime" in user_turn


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


def test_implicit_alias_after_expression_passes_guard(mock_llm):
    # `SUM(x) alias` without AS — superstore eval exposed this false positive
    mock_llm(payload={"status": "ok",
                      "sql": "SELECT o.customer_id, SUM(o.total) average_total "
                             "FROM orders o GROUP BY o.customer_id",
                      "assumptions": []})
    out = generate_sql("average order total per customer?", SCHEMA)
    assert out["status"] == "ok", out


def test_sqlite_date_functions_pass_guard(mock_llm):
    mock_llm(payload={"status": "ok",
                      "sql": "SELECT AVG(julianday(orders.order_date)) FROM orders",
                      "assumptions": []})
    out = generate_sql("average order date?", SCHEMA)
    assert out["status"] == "ok", out


def test_identifier_after_comma_is_still_flagged(mock_llm):
    # the implicit-alias rule must not whitelist invented names in select lists
    mock_llm(payload={"status": "ok",
                      "sql": "SELECT COUNT(*), invented_thing FROM orders",
                      "assumptions": []})
    out = generate_sql("how many orders?", SCHEMA)
    assert out["status"] == "error" and out["error"]["type"] == "invented_identifier"
    assert "invented_thing" in out["error"]["message"]


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


# ---- Part 5: clarification / ambiguity handling contract ----

def test_missing_time_period_returns_clarification(mock_llm):
    mock_llm(payload={"status": "clarification_needed", "sql": None, "assumptions": [],
                      "clarification_question": "Which time period should 'recent' cover?"})
    out = generate_sql("Show recent sales by region", SCHEMA)
    assert out["status"] == "clarification_needed"
    assert "time period" in out["clarification_question"].lower()


def test_metric_not_in_schema_returns_clarification(mock_llm):
    mock_llm(payload={"status": "clarification_needed", "sql": None,
                      "assumptions": ["'profit' has no direct column"],
                      "clarification_question": "Profit = total minus cost? cost isn't stored."})
    out = generate_sql("What was our profit last quarter?", SCHEMA)
    assert out["status"] == "clarification_needed"
    assert out["assumptions"] == ["'profit' has no direct column"]


def test_clarification_never_leaks_sql(mock_llm):
    """Model misbehaving (clarification + sql together) must not pass SQL through."""
    mock_llm(payload={"status": "clarification_needed", "sql": "SELECT total FROM orders",
                      "clarification_question": "Per order or per customer?"})
    out = generate_sql("How much do customers spend?", SCHEMA)
    assert out["status"] == "clarification_needed"
    assert out["sql"] is None


def test_immaterial_defaults_recorded_as_assumptions(mock_llm):
    mock_llm(payload={"status": "ok",
                      "sql": "SELECT region, SUM(total) AS total FROM orders "
                             "JOIN customers ON orders.customer_id = customers.id "
                             "GROUP BY region ORDER BY total DESC LIMIT 10",
                      "assumptions": ["defaulted sort to highest first",
                                      "limited to top 10 regions"]})
    out = generate_sql("sales by region", SCHEMA)
    assert out["status"] == "ok"
    assert len(out["assumptions"]) == 2


def test_non_list_assumptions_coerced_to_empty(mock_llm):
    mock_llm(payload={"status": "ok", "sql": "SELECT total FROM orders",
                      "assumptions": "just one big string"})
    out = generate_sql("show totals", SCHEMA)
    assert out["status"] == "ok" and out["assumptions"] == []


def test_prompt_defines_material_ambiguity_criteria():
    """Guard: the criteria list must stay in the system prompt."""
    prompt = sql_generator.SYSTEM_PROMPT
    for phrase in ("time period", "metric", "clarifying question", "assumptions"):
        assert phrase in prompt
