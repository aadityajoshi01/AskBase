"""Regression tests for the audit fixes: clarification policy, CTE-aware and
table-aware identifier guards, FK-graph join validation, postal_code typing.
All LLM access is mocked — no model or GPU needed."""
import pytest

from backend import sql_generator
from backend.repair_loop import generate_validated_sql
from backend.sql_generator import (
    SYSTEM_PROMPT, find_qualified_column_problems, find_unknown_identifiers,
    find_unvalidated_joins, foreign_key_edges, generate_sql,
)

SCHEMA = {
    "dialect": "sqlite",
    "tables": [
        {"name": "orders", "columns": [
            {"name": "order_id", "type": "INTEGER", "pk": True},
            {"name": "customer_id", "type": "INTEGER", "fk_to": "customers.customer_id"},
            {"name": "total", "type": "REAL"}]},
        {"name": "customers", "columns": [
            {"name": "customer_id", "type": "INTEGER", "pk": True},
            {"name": "name", "type": "TEXT"},
            {"name": "region", "type": "TEXT"}]},
        {"name": "line_items", "columns": [
            {"name": "id", "type": "INTEGER", "pk": True},
            {"name": "order_id", "type": "INTEGER", "fk_to": "orders.order_id"},
            {"name": "sales", "type": "REAL"}]},
    ],
}


def patch_completion(monkeypatch, payload):
    monkeypatch.setattr(sql_generator.llm_client, "complete_json",
                        lambda messages, max_tokens=None: payload)


# --- item 1: unanswerable questions must not be answered with a "closest" query ---

def test_prompt_no_longer_allows_closest_select_for_unanswerable():
    assert "NEVER emit a \"closest\" SELECT" in SYSTEM_PROMPT
    assert "closest SELECT and explain the gap" not in SYSTEM_PROMPT


def test_unanswerable_clarification_from_model_propagates_without_sql(monkeypatch):
    patch_completion(monkeypatch, {"status": "clarification_needed", "sql": None,
                                  "assumptions": [],
                                  "clarification_question":
                                      "The schema has no shipping-cost data — what should I use?"})
    out = generate_sql("What was the average shipping cost?", SCHEMA)
    assert out["status"] == "clarification_needed"
    assert out["sql"] is None
    assert out["error"] is None


# --- item 5/2: CTE-aware identifier guard ---

def test_valid_cte_query_is_not_flagged_as_invented():
    sql = ("WITH monthly AS (SELECT strftime('%Y-%m', o.order_date) AS ym, "
           "SUM(o.total) AS s FROM orders o GROUP BY ym) "
           "SELECT ym, s FROM monthly")
    SCHEMA_WITH_DATE = {
        "tables": [{"name": "orders", "columns": [
            {"name": "order_id", "type": "INTEGER"}, {"name": "order_date", "type": "TEXT"},
            {"name": "total", "type": "REAL"}]}],
    }
    assert find_unknown_identifiers(sql, SCHEMA_WITH_DATE) == []


def test_cte_output_column_rejects_wrong_qualified_reference():
    sql = "WITH m AS (SELECT total AS s FROM orders) SELECT m.s, m.nope FROM m"
    assert find_qualified_column_problems(sql, SCHEMA) == \
        ["m.nope — WITH m does not produce it"]


# --- item 2: table-aware identifiers, aggregate subject, FK-graph joins ---

def test_qualified_column_on_wrong_table_is_rejected():
    assert find_qualified_column_problems("SELECT orders.name FROM orders", SCHEMA) == \
        ["orders.name — orders has no such column"]


def test_aggregate_subject_checked_against_its_table():
    # sales lives on line_items, not orders — SUM(orders.sales) is the audit case
    problems = find_qualified_column_problems("SELECT SUM(orders.sales) FROM orders", SCHEMA)
    assert problems and "orders" in problems[0]


def test_valid_columns_and_qualifies_pass():
    assert find_qualified_column_problems(
        "SELECT c.region, SUM(o.total) FROM orders o JOIN customers c "
        "ON o.customer_id = c.customer_id GROUP BY c.region", SCHEMA) == []


def test_fk_edges_are_symmetric_pairs():
    assert foreign_key_edges(SCHEMA) == {
        frozenset((("orders", "customer_id"), ("customers", "customer_id"))),
        frozenset((("line_items", "order_id"), ("orders", "order_id")))}


def test_invented_join_condition_is_rejected():
    bad = "SELECT COUNT(*) FROM orders JOIN customers ON orders.order_id = customers.name"
    assert find_unvalidated_joins(bad, SCHEMA)
    ok = "SELECT COUNT(*) FROM orders JOIN customers ON orders.customer_id = customers.customer_id"
    assert find_unvalidated_joins(ok, SCHEMA) == []


def test_chained_fk_joins_pass():
    sql = ("SELECT SUM(l.sales) FROM line_items l "
           "JOIN orders o ON l.order_id = o.order_id "
           "JOIN customers c ON o.customer_id = c.customer_id")
    assert find_unvalidated_joins(sql, SCHEMA) == []


def test_schema_without_fk_metadata_disables_join_check():
    only = {"tables": [{"name": "t", "columns": [{"name": "a", "type": "INTEGER"},
                                                  {"name": "b", "type": "INTEGER"}]}]}
    assert find_unvalidated_joins(
        "SELECT COUNT(*) FROM t t1 JOIN t t2 ON t1.a = t2.b", only) == []


def test_generate_sql_returns_unvalidated_join_error(monkeypatch):
    patch_completion(monkeypatch, {
        "status": "ok",
        "sql": "SELECT COUNT(*) FROM orders JOIN customers ON orders.total = customers.name",
        "assumptions": [], "clarification_question": None})
    out = generate_sql("how many orders per customer?", SCHEMA)
    assert out["status"] == "error"
    assert out["error"]["type"] == "unvalidated_join"


def test_repair_loop_retries_unvalidated_join_and_succeeds(monkeypatch):
    seq = iter([
        {"status": "ok", "sql": "SELECT COUNT(*) FROM orders JOIN customers ON orders.total = customers.name"},
        {"status": "ok", "sql": "SELECT COUNT(*) FROM orders JOIN customers ON orders.customer_id = customers.customer_id"},
    ])
    monkeypatch.setattr(sql_generator.llm_client, "complete_json",
                        lambda messages, max_tokens=None: next(seq))
    out = generate_validated_sql("q", SCHEMA, lambda sql, dialect: {"valid": True})
    assert out["status"] == "ok" and out["attempts"] == 2


def test_repair_loop_retries_qualified_column_error(monkeypatch):
    seq = iter([
        {"status": "ok", "sql": "SELECT orders.name FROM orders"},
        {"status": "ok", "sql": "SELECT c.name FROM orders o JOIN customers c ON o.customer_id = c.customer_id"},
    ])
    monkeypatch.setattr(sql_generator.llm_client, "complete_json",
                        lambda messages, max_tokens=None: next(seq))
    out = generate_validated_sql(
        "q", SCHEMA, lambda sql, dialect: {"valid": True})
    assert out["status"] == "ok" and out["attempts"] == 2


# --- item 6: postal_code typing in the demo datasets ---

def test_postal_helper_preserves_leading_zeros():
    from datasets.build_datasets import _postal_or_null
    assert _postal_or_null("1550") == "01550"
    assert _postal_or_null("01550") == "01550"
    assert _postal_or_null("42420") == "42420"
    assert _postal_or_null("") is None
    assert _postal_or_null(None) is None


def test_demo_superstore_postal_code_is_text_with_zeros():
    import pathlib
    import sqlite3
    db = pathlib.Path(__file__).resolve().parents[1] / "datasets" / "superstore.db"
    if not db.exists():
        pytest.skip("superstore.db not built in this checkout")
    con = sqlite3.connect(db)
    types = {row[0] for row in con.execute(
        "SELECT typeof(postal_code) FROM customers WHERE postal_code IS NOT NULL")}
    zeros = con.execute(
        "SELECT COUNT(*) FROM customers WHERE postal_code LIKE '0%'").fetchone()[0]
    con.close()
    assert types == {"text"}
    assert zeros == 34  # the migrated 4-digit ZIPs, leading zeros restored


def test_fixture_schema_declares_postal_code_text():
    from datasets.question_sets import SUPERSTORE
    postal = next(c for c in SUPERSTORE["schema"]["tables"][0]["columns"]
                  if c["name"] == "postal_code")
    assert postal["type"] == "TEXT"
