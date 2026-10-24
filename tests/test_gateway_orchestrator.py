"""End-to-end gateway tests — real Agent 2 discovery, real Agent 3 execution,
mocked LLM only. These prove the pipeline composition itself."""
import json
import sqlite3
from pathlib import Path

import pytest

import gateway.orchestrator as orch


@pytest.fixture()
def demo_db(tmp_path: Path) -> str:
    path = tmp_path / "demo.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, region TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER,
                             order_date DATE, total REAL);
        INSERT INTO customers VALUES (1,'Ana','West'), (2,'Bo','East'), (3,'Cy','West');
        INSERT INTO orders VALUES (1,1,'2024-01-05',100.0), (2,2,'2024-02-01',50.0),
                                  (3,1,'2024-02-20',25.0), (4,3,'2024-03-04',200.0);
        """
    )
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture()
def registered(demo_db, monkeypatch):
    entry = {"id": "demo", "label": "Demo", "type": "sqlite", "path": demo_db,
             "build": "test fixture"}
    monkeypatch.setattr(orch, "load_registry", lambda: {"demo": entry})
    return entry


def fake_llm(responses):
    """chat_completion stand-in popping scripted replies (JSON strings)."""
    remaining = list(responses)

    def _call(messages, max_tokens=None):
        reply = remaining.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)

    return _call


def sql_reply(sql, **extra):
    return {"status": "ok", "sql": sql, "assumptions": extra.get("assumptions", []),
            "clarification_question": None}


def test_happy_path_executes_and_verifies(registered, monkeypatch):
    monkeypatch.setattr(orch.agents.llm_client, "chat_completion",
                        fake_llm([sql_reply("SELECT c.region, SUM(o.total) AS revenue "
                                            "FROM orders o JOIN customers c ON c.id=o.customer_id "
                                            "GROUP BY c.region ORDER BY revenue DESC"),
                                  "West leads with 325.0 in total revenue."]))
    result = orch.run_analysis("What is total revenue by region?", {"database": "demo"})
    assert result["status"] == "success"
    assert result["verification"]["passed"] is True
    assert result["rowCount"] == 2
    assert result["answer"] == "West leads with 325.0 in total revenue."
    assert result["chart"] == {"type": "bar", "x": "region", "y": "revenue"}
    assert result["meta"]["readOnly"] is True
    assert result["meta"]["code"] == "success"


def test_repair_loop_recovers_from_bad_first_sql(registered, monkeypatch):
    monkeypatch.setattr(orch.agents.llm_client, "chat_completion",
                        fake_llm([sql_reply("SELECT region, SUM(total) FROM nonexistent_table GROUP BY region"),
                                  sql_reply("SELECT region, COUNT(*) AS n FROM customers GROUP BY region"),
                                  "Two customers in West, one in East."]))
    result = orch.run_analysis("How many customers per region?", {"database": "demo"})
    assert result["status"] == "success"
    assert result["attempts"] == 2


def test_write_sql_never_reaches_the_database(registered, monkeypatch):
    delete = {"status": "ok", "sql": "DELETE FROM orders",
              "assumptions": [], "clarification_question": None}
    monkeypatch.setattr(orch.agents.llm_client, "chat_completion", fake_llm([delete] * 3))
    result = orch.run_analysis("Delete the orders.", {"database": "demo"})
    assert result["status"] == "error"
    assert result["meta"]["code"] == "unsafe_sql"
    conn = sqlite3.connect(registered["path"])
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 4
    conn.close()


def test_clarification_short_circuits_without_execution(registered, monkeypatch):
    monkeypatch.setattr(orch.agents.llm_client, "chat_completion",
                        fake_llm([{"status": "clarification_needed", "sql": None,
                                   "assumptions": [],
                                   "clarification_question": "Which time period do you mean?"}]))
    result = orch.run_analysis("How have sales been doing recently?", {"database": "demo"})
    assert result["status"] == "clarification_needed"
    assert result["clarification_question"] == "Which time period do you mean?"
    assert result["sql"] == ""
    assert result["rowCount"] == 0


UI_REQUIRED = ["status", "answer", "sql", "columns", "rows", "rowCount",
               "truncated", "warnings", "verification", "chart", "meta"]


def test_every_envelope_satisfies_the_ui_schema(registered, monkeypatch):
    """agent4 validates responses against schemas/analysis-response.schema.json."""
    def check(result):
        for field in UI_REQUIRED:
            assert field in result, field
        assert isinstance(result["sql"], str)
        assert isinstance(result["answer"], str)
        assert result["status"] in ("success", "clarification_needed", "error")
        assert isinstance(result["verification"]["passed"], bool)
        assert isinstance(result["verification"]["message"], str)

    monkeypatch.setattr(orch.agents.llm_client, "chat_completion",
                        fake_llm([{"status": "clarification_needed", "sql": None,
                                   "assumptions": [], "clarification_question": "When?"}]))
    check(orch.run_analysis("q", {"database": "demo"}))
    check(orch.run_analysis("q", {"database": "missing"}))
    monkeypatch.setattr(orch.agents.llm_client, "chat_completion",
                        fake_llm([orch.agents.llm_client.LLMError("offline", "down")]))
    check(orch.run_analysis("q", {"database": "demo"}))
    monkeypatch.setattr(orch.agents.llm_client, "chat_completion",
                        fake_llm([sql_reply("SELECT region FROM customers"), "answer text"]))
    check(orch.run_analysis("q", {"database": "demo"}))


def test_offline_llm_degrades_cleanly(registered, monkeypatch):
    from gateway.orchestrator import agents
    error = orch.agents.llm_client.LLMError("offline", "LLM server unreachable")
    monkeypatch.setattr(orch.agents.llm_client, "chat_completion", fake_llm([error]))
    result = orch.run_analysis("Any question", {"database": "demo"})
    assert result["status"] == "error"
    assert result["meta"]["code"] == "model_offline"
    assert result["verification"]["passed"] is False


def test_empty_result_is_a_success(registered, monkeypatch):
    monkeypatch.setattr(orch.agents.llm_client, "chat_completion",
                        fake_llm([sql_reply("SELECT * FROM orders WHERE total > 100000")]))
    result = orch.run_analysis("Orders above 100000?", {"database": "demo"})
    assert result["status"] == "success"
    assert result["rowCount"] == 0
    assert "no rows" in result["answer"].lower()


def test_unknown_database_returns_error_envelope():
    result = orch.run_analysis("anything", {"database": "not_registered"})
    assert result["status"] == "error"
    assert "not registered" in result["verification"]["message"]


def test_missing_database_field_is_handled():
    result = orch.run_analysis("anything", None)
    assert result["status"] == "error"


def test_chart_requires_two_columns_with_numeric_second(registered):
    assert orch._choose_chart(["region", "revenue"], [["West", 1.0], ["East", 2.0]])["type"] == "bar"
    assert orch._choose_chart(["region", "name"], [["West", "a"], ["East", "b"]]) is None
    assert orch._choose_chart(["a", "b", "c"], [[1, 2, 3], [4, 5, 6]]) is None
    assert orch._choose_chart(["a", "b"], [[1, 2.0]]) is None


def test_role_names_are_scrubbed_from_model_text_for_restricted_callers(registered, monkeypatch):
    monkeypatch.setattr(orch.agents.llm_client, "chat_completion", fake_llm([
        {"status": "clarification_needed", "sql": None,
         "assumptions": ["region column is hidden from role 'viewer'"],
         "clarification_question": "Which region do you mean? Some rows are hidden "
                                   "from role 'viewer'."}]))
    result = orch.run_analysis("Revenue by region?", {"database": "demo"})
    assert result["status"] == "clarification_needed"
    assert "viewer" not in result["clarification_question"]
    assert "viewer" not in " ".join(result["assumptions"])
    assert "your access level" in result["clarification_question"]


def test_time_axis_becomes_a_line_only_when_it_is_ordered(registered):
    months = [["2024-01", 10.0], ["2024-02", 12.0], ["2024-03", 9.5]]
    assert orch._choose_chart(["month", "revenue"], months) == {
        "type": "line", "x": "month", "y": "revenue"}
    assert orch._choose_chart(["day", "units"],
                              [["2024-01-05", 1.0], ["2024-01-04", 2.0]])["type"] == "bar"
    assert orch._choose_chart(["year", "total"], [["2015", 1.0], ["2016", 2.0]])["type"] == "line"
    # "Q1"/"East"/None are not periods; a bar must not be relabelled as a trend
    assert orch._choose_chart(["quarter", "total"], [["Q1", 1.0], ["Q2", 2.0]])["type"] == "bar"
    assert orch._choose_chart(["ordered", "total"], [[None, 1.0], ["2024-01", 2.0]])["type"] == "bar"


def engine_failure(reason: str = "invalid_sql", message: str = "no such function") -> dict:
    return {"status": "error", "sql": "", "dialect": "sqlite", "columns": [], "rows": [],
            "row_count": 0, "truncated": False, "warnings": [], "elapsed_ms": 1.0,
            "error": {"type": reason, "message": message}}


class FakeGenerate:
    """Stands in for agents.generate_sql, recording each prior_error it was prompted with."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prior_errors = []

    def __call__(self, question, schema, prior_error=None):
        self.prior_errors.append(prior_error)
        return self.replies.pop(0)


def test_engine_rejection_reuses_the_repair_budget(registered, monkeypatch):
    """A query the validator accepts but SQLite rejects must not end the analysis
    while attempts remain — the engine's reason is fed back to the model."""
    bad = sql_reply("SELECT EXTRACT(YEAR FROM order_date) AS yr FROM orders")
    good = sql_reply("SELECT region, COUNT(*) AS n FROM customers GROUP BY region")

    real_execute = orch.agents.execute_query
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return engine_failure() if len(calls) == 1 else real_execute(sql, config)

    generate = FakeGenerate([bad, good])
    monkeypatch.setattr(orch.agents.llm_client, "chat_completion", fake_llm(["Two regions."]))
    monkeypatch.setattr(orch.agents, "generate_sql", generate)
    monkeypatch.setattr(orch.agents, "execute_query", execute)
    result = orch.run_analysis("Orders per year?", {"database": "demo"})
    assert result["status"] == "success"
    assert len(calls) == 2
    assert result["attempts"] == 2
    # the engine's reason, not just "try again", is what the model sees next
    assert generate.prior_errors[1]["type"] == "invalid_sql"
    assert "no such function" in generate.prior_errors[1]["message"]


def test_engine_repair_stops_at_the_shared_cap(registered, monkeypatch):
    """These bypass Agent 1's generator on purpose: the subject under test is the
    gateway's shared budget, not which layer first rejects the SQL."""
    bad = sql_reply("SELECT EXTRACT(YEAR FROM order_date) AS yr FROM orders")
    monkeypatch.setattr(orch.agents, "generate_sql", FakeGenerate([bad] * 3))
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return engine_failure()

    monkeypatch.setattr(orch.agents, "execute_query", execute)
    result = orch.run_analysis("Orders per year?", {"database": "demo"})
    assert result["status"] == "error"
    assert len(calls) == 3                      # 1 initial + 2 repairs, never a 4th
    assert result["attempts"] == 3
    assert result["meta"]["code"] == "unsafe_sql"


def test_infrastructure_failure_is_not_re_prompted(registered, monkeypatch):
    """A locked/unreadable database is not the model's mistake — no retries."""
    ok = sql_reply("SELECT region FROM customers")
    generate = FakeGenerate([ok] * 3)
    monkeypatch.setattr(orch.agents, "generate_sql", generate)
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return engine_failure("database_unavailable", "the database is locked by another writer")

    monkeypatch.setattr(orch.agents, "execute_query", execute)
    result = orch.run_analysis("Regions?", {"database": "demo"})
    assert result["status"] == "error"
    assert len(calls) == 1
    assert generate.prior_errors == [None]      # the model was never re-asked
    assert result["meta"]["code"] == "database_unavailable"
    assert result["error"]["detail"] == "database_unavailable"
