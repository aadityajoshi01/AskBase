"""Gateway HTTP surface tests — server starts, routes respond, UI contract holds."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from gateway.server import GatewayHandler


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(base, path, user=None):
    request = urllib.request.Request(base + path,
                                     headers={"X-AskBase-User": user} if user else {})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def post(base, path, payload):
    request = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def http_error_body(exc: urllib.error.HTTPError) -> dict:
    """HTTPError holds an open response; unread sockets warn at interpreter shutdown."""
    with exc:
        return json.loads(exc.read())


def test_health(server):
    assert get(server, "/api/health")["status"] == "ok"


def test_databases_lists_registered(server):
    body = get(server, "/api/databases")
    ids = {entry["id"] for entry in body["databases"]}
    assert {"sample_retail", "superstore", "cmapss"} <= ids


def test_users_route_lists_only_roles_the_server_would_honour(server):
    """The UI builds its role switcher from this, so it must never offer a role the
    gateway would not apply — and it must not invent an admin for an anonymous caller."""
    body = get(server, "/api/users")
    assert {entry["role"] for entry in body["users"]} <= {"admin", "analyst", "viewer"}
    assert body["you"]["role"] == "viewer"          # unknown identity fails closed
    assert body["you"]["known_user"] is False

    as_admin = get(server, "/api/users", user="admin")
    assert as_admin["you"]["role"] == "admin"
    assert {"users", "role_limits"} <= set(as_admin)


def test_schema_requires_registered_database(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(server, "/api/schema?database=not_here")
    assert exc.value.code == 404
    assert http_error_body(exc.value)["status"] == "error"


def test_insights_returns_model_questions_and_summary(server, monkeypatch):
    from gateway import orchestrator

    monkeypatch.setattr(orchestrator.agents.llm_client, "complete_json", lambda messages, max_tokens=None: {
        "questions": ["Which region sells the most?"],
        "summary": "Orders joined to customers, one row per order line.",
    })
    body = get(server, "/api/insights?database=sample_retail")
    assert body["status"] == "success"
    assert body["questions"] == ["Which region sells the most?"]
    assert "customers" in body["context"]
    assert body["table_count"] >= 1


def test_insights_without_a_model_still_describes_the_schema(server, monkeypatch):
    from gateway import orchestrator

    def offline(messages, max_tokens=None):
        raise orchestrator.agents.llm_client.LLMError("offline", "LLM server unreachable")

    monkeypatch.setattr(orchestrator.agents.llm_client, "complete_json", offline)
    body = get(server, "/api/insights?database=sample_retail")
    assert body["status"] == "error"
    assert body["questions"] == []
    assert body["error"]["type"] == "offline"
    # the UI falls back on this text rather than showing an empty panel
    assert body["context"].startswith("- ")


def test_insights_requires_registered_database(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(server, "/api/insights?database=not_here")
    assert exc.value.code == 404


def test_analyze_unknown_database_still_returns_valid_envelope(server):
    body = post(server, "/api/analyze",
               {"question": "anything", "databaseConfig": {"database": "not_here"}})
    assert body["status"] == "error"
    assert set(body) >= {"status", "answer", "sql", "columns", "rows", "rowCount",
                         "truncated", "warnings", "verification", "chart", "meta"}
    assert body["meta"]["readOnly"] is True


def test_oversized_body_is_refused_after_draining(server):
    """413 must arrive as JSON: answering while the client is still sending resets
    the socket (WinError 10053) — which is why the body is read before rejecting."""
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(server, "/api/analyze", {"question": "x" * (70 * 1024),
                                      "databaseConfig": {"database": "sample_retail"}})
    assert exc.value.code == 413
    assert "exceeds" in http_error_body(exc.value)["message"]


def test_malformed_json_is_400(server):
    request = urllib.request.Request(
        server + "/api/analyze", data=b"{not json",
        headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=10)
    assert exc.value.code == 400
    assert "invalid JSON" in http_error_body(exc.value)["message"]


def test_unknown_route_is_404(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(server, "/api/nope")
    assert exc.value.code == 404
