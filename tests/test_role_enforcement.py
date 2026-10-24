"""Phase 2 part 2 — roles enforced by the backend, proved through the HTTP routes.

The model is stubbed, so each reply is fixed SQL and the rows that come back are the
engine's answer to the *rewritten* query. Anything a restricted role cannot see has to
be absent from the payload itself, which is what these tests assert.
"""
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import gateway.orchestrator as orch
import gateway.server as server_module
from gateway.server import GatewayHandler

POLICIES = {
    "role_defaults": {
        "admin": {"unrestricted": True},
        "analyst": {"max_rows": 2},
        "viewer": {"mask_name_patterns": ["email"], "max_rows": 200},
    },
    "users": {"root": {"role": "admin", "label": "Data owner"},
              "rita": {"role": "analyst", "label": "Analyst"},
              "amit": {"role": "viewer", "label": "East only"}},
    "datasets": {
        "staff": {
            "viewer": {
                "row_filters": {
                    "customers": {"column": "region", "in": ["East"]},
                    "orders": {"semi_join": {
                        "column": "customer_id", "from_table": "customers",
                        "where": {"column": "region", "in": ["East"]}}}},
                "masked_columns": ["customers.name"],
            },
            "analyst": {"masked_columns": ["customers.postal_code"]},
        }
    },
}

# 4 customers (2 East, 2 West), 10 orders (3 of them East), so every difference
# between the roles is a different number rather than a different wording.
EAST = ("('C1','Ana Torres','East','ana@example.com',400001)",
        "('C2','Bo Chen','East','bo@example.com',400002)")
WEST = ("('C3','Cy Patel','West','cy@example.com',400003)",
        "('C4','Di Rao','West','di@example.com',400004)")
ORDERS = ("('O1','C1',10.0), ('O2','C2',20.0), ('O3','C1',30.0), "
          "('O4','C3',40.0), ('O5','C3',50.0), ('O6','C4',60.0), "
          "('O7','C4',70.0), ('O8','C3',80.0), ('O9','C4',90.0), "
          "('O10','C3',100.0)")


@pytest.fixture()
def staff(tmp_path, monkeypatch):
    path = tmp_path / "staff.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE customers (customer_id TEXT PRIMARY KEY, name TEXT, region TEXT,"
        " email TEXT, postal_code INTEGER);"
        f"INSERT INTO customers VALUES {','.join(EAST + WEST)};"
        "CREATE TABLE orders (order_id TEXT PRIMARY KEY, customer_id TEXT, total REAL);"
        f"INSERT INTO orders VALUES {ORDERS};")
    conn.commit()
    conn.close()
    entry = {"id": "staff", "label": "Staff fixture", "type": "sqlite",
             "path": str(path), "build": "test fixture"}
    monkeypatch.setattr(orch, "load_registry", lambda: {"staff": entry})
    monkeypatch.setattr(orch, "load_policies", lambda *args, **kwargs: POLICIES)
    return entry


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def request(base, path, payload=None, user=None, expect=200):
    headers = {"Content-Type": "application/json"}
    if user:
        headers["X-AskBase-User"] = user
    http = urllib.request.Request(base + path, data=(json.dumps(payload).encode()
                                                    if payload is not None else None),
                                  headers=headers, method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(http, timeout=60) as response:
            assert response.status == expect
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        assert exc.code == expect, exc.read().decode()
        return json.loads(exc.read())


def scripted(sql):
    """Chat stand-in: the model emits `sql`, then narrates whatever the engine returned."""
    replies = [{"status": "ok", "sql": sql, "assumptions": [],
                "clarification_question": None},
               "The rows the engine returned."]

    def fake(messages, max_tokens=None):
        reply = replies.pop(0)
        return reply if isinstance(reply, str) else json.dumps(reply)

    return fake


def analyze(server, sql, user, monkeypatch):
    monkeypatch.setattr(orch.agents.llm_client, "chat_completion", scripted(sql))
    return request(server, "/api/analyze",
                   {"question": "Give me the table.", "databaseConfig": {"database": "staff"}},
                   user=user)


# ------------------------------------------------------------------ rows

def test_an_admin_reads_every_row(staff, server, monkeypatch):
    result = analyze(server, "SELECT customer_id, region FROM customers ORDER BY customer_id",
                     "root", monkeypatch)
    assert [row[0] for row in result["rows"]] == ["C1", "C2", "C3", "C4"]
    assert result["meta"]["role"] == "admin"
    assert result["meta"]["restrictions"].get("predicates") == {}


def test_a_viewer_never_receives_a_row_outside_its_scope(staff, server, monkeypatch):
    result = analyze(server, "SELECT customer_id, region FROM customers ORDER BY customer_id",
                     "amit", monkeypatch)
    assert [row[0] for row in result["rows"]] == ["C1", "C2"]
    assert {row[1] for row in result["rows"]} == {"East"}


def test_a_count_can_only_count_permitted_rows(staff, server, monkeypatch):
    admin = analyze(server, "SELECT COUNT(*) FROM orders", "root", monkeypatch)
    viewer = analyze(server, "SELECT COUNT(*) FROM orders", "amit", monkeypatch)
    assert admin["rows"] == [[10]]
    assert viewer["rows"] == [[3]]


def test_a_join_is_filtered_on_both_sides(staff, server, monkeypatch):
    result = analyze(server,
                     "SELECT o.order_id, c.region FROM orders o "
                     "JOIN customers c ON c.customer_id = o.customer_id ORDER BY o.order_id",
                     "amit", monkeypatch)
    assert [row[0] for row in result["rows"]] == ["O1", "O2", "O3"]
    assert {row[1] for row in result["rows"]} == {"East"}


def test_an_aggregate_groups_only_over_permitted_rows(staff, server, monkeypatch):
    result = analyze(server,
                     "SELECT customers.region, SUM(orders.total) AS revenue FROM orders "
                     "JOIN customers ON customers.customer_id = orders.customer_id "
                     "GROUP BY customers.region ORDER BY revenue DESC",
                     "amit", monkeypatch)
    assert result["rows"] == [["East", 60.0]]


def test_a_role_row_cap_truncates_before_the_ui_sees_it(staff, server, monkeypatch):
    result = analyze(server, "SELECT order_id FROM orders ORDER BY order_id", "rita", monkeypatch)
    assert result["rowCount"] == 2
    assert result["truncated"] is True


# ---------------------------------------------------------------- columns

def test_select_star_cannot_reach_a_masked_column(staff, server, monkeypatch):
    result = analyze(server, "SELECT * FROM customers ORDER BY customer_id", "amit", monkeypatch)
    assert result["status"] == "success"
    assert "name" not in result["columns"]
    assert "Ana Torres" not in json.dumps(result)


def test_naming_a_masked_column_fails_instead_of_leaking_it(staff, server, monkeypatch):
    result = analyze(server, "SELECT customer_id, name FROM customers", "amit", monkeypatch)
    assert result["status"] != "success"
    assert "Torres" not in json.dumps(result)


def test_a_column_hidden_by_name_pattern_disappears_from_the_schema(staff, server, monkeypatch):
    body = request(server, "/api/schema?database=staff", user="amit")
    # the DDL summary would have listed it, so the whole summary is withheld
    assert body["summary_text"] == ""
    assert "email" not in json.dumps(body["tables"])
    admin = request(server, "/api/schema?database=staff", user="root")
    assert "email" in json.dumps(admin["tables"])


# ------------------------------------------------------------ metadata

def test_the_envelope_reports_the_limit_that_was_applied(staff, server, monkeypatch):
    result = analyze(server, "SELECT customer_id FROM customers", "amit", monkeypatch)
    restrictions = result["meta"]["restrictions"]
    assert restrictions["role"] == "viewer"
    assert restrictions["user"] == "amit"
    assert restrictions["hidden_columns"] == ["customers.email", "customers.name"]
    # only the tables the query actually touched are reported as filtered
    assert set(restrictions["predicates"]) == {"customers"}
    assert "region IN" in restrictions["predicates"]["customers"]
    assert result["warnings"][0].startswith("Role 'viewer' policy on staff")


def test_the_schema_route_restates_the_size_the_role_may_read(staff, server):
    tables = {t["name"]: t for t in request(server, "/api/schema?database=staff",
                                            user="amit")["tables"]}
    assert tables["customers"]["row_count"] == 2
    assert tables["customers"]["row_count_withheld"] is False
    admin = {t["name"]: t for t in request(server, "/api/schema?database=staff",
                                          user="root")["tables"]}
    assert admin["customers"]["row_count"] == 4


def test_the_profile_route_withholds_the_true_table_size(staff, server):
    body = request(server, "/api/datasets/profile", {"database": "staff"}, user="amit")
    text = json.dumps(body)
    assert "Ana Torres" not in text
    customers = {t["name"]: t for t in body["tables"]}["customers"]
    assert customers["row_count"] is None
    assert "name" not in [column["name"] for column in customers["columns"]]


def test_the_insights_context_never_names_a_hidden_column(staff, server, monkeypatch):
    monkeypatch.setattr(orch.agents.llm_client, "complete_json", lambda messages, max_tokens=None: {
        "questions": ["How many customers are there?"], "summary": "Customers and orders."})
    body = request(server, "/api/insights?database=staff", user="amit")
    assert body["role"] == "viewer"
    assert "name:" not in body["context"]
    assert "(2 rows)" in body["context"]
    assert "email" not in body["context"]


# ------------------------------------------------------------ fail closed

def test_an_unknown_user_gets_the_most_restricted_role(staff, server, monkeypatch):
    result = analyze(server, "SELECT customer_id FROM customers ORDER BY customer_id",
                     "someone-absent", monkeypatch)
    assert result["meta"]["role"] == "viewer"
    assert [row[0] for row in result["rows"]] == ["C1", "C2"]


def test_an_unregistered_database_is_not_a_menu_of_what_exists(staff, server):
    result = request(server, "/api/analyze",
                     {"question": "anything", "databaseConfig": {"database": "nope"}})
    assert result["status"] == "error"
    assert "staff" not in result["verification"]["message"]


def test_a_gateway_crash_still_returns_a_validatable_envelope(staff, server, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("generator crashed")

    monkeypatch.setattr(server_module, "run_analysis", explode)
    body = request(server, "/api/analyze",
                   {"question": "anything", "databaseConfig": {"database": "staff"}},
                   user="amit")
    for field in ("status", "answer", "sql", "columns", "rows", "rowCount", "truncated",
                  "warnings", "verification", "chart", "meta"):
        assert field in body, field
    assert body["sql"] == ""                      # the schema types sql as a string
    assert body["meta"]["role"] == "viewer"


# ------------------------------------------------- the shipped policies.json

SUPERSTORE_DB = Path(orch.ROOT) / "ai-analyst" / "datasets" / "superstore.db"


def _superstore(server, sql, user, monkeypatch):
    monkeypatch.setattr(orch.agents.llm_client, "chat_completion", scripted(sql))
    return request(server, "/api/analyze",
                   {"question": "Customers per region?",
                    "databaseConfig": {"database": "superstore"}}, user=user)


@pytest.mark.skipif(not SUPERSTORE_DB.is_file(),
                    reason="run ai-analyst/datasets/build_datasets.py first")
def test_the_shipped_viewer_policy_holds_on_the_real_superstore(server, monkeypatch):
    """The demo policy the UI ships with, measured against the real 793-customer table."""
    sql = "SELECT region, COUNT(*) AS customers FROM customers GROUP BY region ORDER BY region"
    viewer = _superstore(server, sql, "amit", monkeypatch)
    assert viewer["rows"] == [["East", 220]]
    assert sum(row[1] for row in viewer["rows"]) == 220

    admin = _superstore(server, sql, "admin", monkeypatch)
    assert [row[0] for row in admin["rows"]] == ["Central", "East", "South", "West"]
    assert sum(row[1] for row in admin["rows"]) == 793


@pytest.mark.skipif(not SUPERSTORE_DB.is_file(),
                    reason="run ai-analyst/datasets/build_datasets.py first")
def test_the_shipped_policy_names_only_columns_that_exist(server):
    for role_user, hidden in (("amit", ("name", "postal_code")), ("rita", ("postal_code",))):
        tables = request(server, "/api/schema?database=superstore", user=role_user)["tables"]
        customers = {t["name"]: t for t in tables}["customers"]
        names = {column["name"] for column in customers["columns"]}
        for column in hidden:
            assert column not in names, f"{role_user} can still see {column}"
