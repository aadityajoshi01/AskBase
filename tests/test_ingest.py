"""CSV ingestion tests — an uploaded file must become a real, registered, queryable
dataset, and anything hostile inside it must stay data."""
import json
import sqlite3
from pathlib import Path

import pytest

import gateway.ingest as ingest
import gateway.orchestrator as orch

CSV = ("Order Id,Region,Total,Placed On,Note\n"
       "1,West,120.50,2024-01-05,ok\n"
       "2,East,70,2024-02-01,'); DROP TABLE orders;--\n"
       "3,West,,2024-03-09,missing total\n")


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    """Isolate ingestion from the repository's real uploads directory."""
    directory = tmp_path / "uploads"
    monkeypatch.setattr(ingest, "UPLOAD_DIR", directory)
    monkeypatch.setattr(ingest, "INDEX_FILE", directory / "index.json")
    return directory


def db(entry) -> sqlite3.Connection:
    return sqlite3.connect(Path(ingest.UPLOAD_DIR) / f"{entry['id']}.db")


def sql_reply(sql, **extra):
    return {"status": "ok", "sql": sql, "assumptions": extra.get("assumptions", []),
            "clarification_question": None}


def test_build_creates_a_typed_queryable_table(uploads):
    entry = ingest.build_dataset("sales.csv", CSV)
    conn = db(entry)
    assert conn.execute(f'SELECT COUNT(*) FROM "{entry["table"]}"').fetchone()[0] == 3
    columns = {row[1]: row[2] for row in conn.execute(f'PRAGMA table_info("{entry["table"]}")')}
    assert list(columns) == ["order_id", "region", "total", "placed_on", "note"]
    assert columns["order_id"] == "INTEGER"
    assert columns["total"] == "REAL"        # one blank cell does not demote the column
    assert columns["placed_on"] == "TEXT"    # dates stay TEXT, as Agent 2 reports them
    conn.close()


def test_a_sql_payload_in_a_cell_stays_data(uploads):
    entry = ingest.build_dataset("sales.csv", CSV)
    conn = db(entry)
    note = conn.execute(f'SELECT note FROM "{entry["table"]}" WHERE order_id = 2').fetchone()[0]
    assert note == "'); DROP TABLE orders;--"
    assert [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")] == [entry["table"]]
    conn.close()


def test_headers_are_sanitized_and_deduplicated(uploads):
    entry = ingest.build_dataset("weird.csv", "a,'); DROP TABLE x;--,b,b\n1,2,3,4\n")
    names = [column["name"] for column in entry["columns"]]
    assert names == ["a", "drop_table_x", "b", "b_2"]
    assert all(name.isidentifier() and name.islower() for name in names)
    conn = db(entry)
    assert conn.execute(f'SELECT COUNT(*) FROM "{entry["table"]}"').fetchone()[0] == 1
    conn.close()


def test_reuploading_the_same_file_replaces_it(uploads):
    first = ingest.build_dataset("sales.csv", CSV)
    again = ingest.build_dataset("sales.csv", CSV)
    assert first["id"] == again["id"]
    assert len(list(Path(uploads).glob("*.db"))) == 1
    assert len(ingest.load_uploads()) == 1


@pytest.mark.parametrize("bad, reason", [
    ("", "empty"),
    ("   \n \n", "empty"),
    ("only,headers\n", "no data row"),
])
def test_unusable_input_is_refused_before_any_file_is_written(uploads, bad, reason):
    with pytest.raises(ingest.IngestError):
        ingest.build_dataset("bad.csv", bad)
    assert not Path(uploads).exists() or list(Path(uploads).glob("*.db")) == []


def test_unnamed_headers_get_positional_names(uploads):
    entry = ingest.build_dataset("partial.csv", "a,,b\n1,2,3\n")
    assert [column["name"] for column in entry["columns"]] == ["a", "column_2", "b"]


def test_oversized_shapes_are_refused(uploads):
    wide = ",".join(f"c{i}" for i in range(ingest.MAX_COLUMNS + 1))
    with pytest.raises(ingest.IngestError):
        ingest.parse_csv(f"{wide}\n1,2\n")
    with pytest.raises(ingest.IngestError):
        ingest.parse_csv("a,b\n" + "1,2\n" * (ingest.MAX_ROWS + 1))
    with pytest.raises(ingest.IngestError):
        ingest.parse_csv("a\n" + ("x" * (ingest.MAX_CELL_CHARS + 100) + "\n"))


def test_short_rows_are_padded_not_rejected(uploads):
    entry = ingest.build_dataset("ragged.csv", "a,b,c\n1,2\n3,4,5\n")
    conn = db(entry)
    assert conn.execute(f'SELECT c FROM "{entry["table"]}" ORDER BY a').fetchall() == [(None,), (5,)]
    conn.close()


def test_blank_cells_are_missing_not_zero(uploads):
    """'' casts to 0 inside SUM/AVG — a missing total must not become free revenue."""
    entry = ingest.build_dataset("sales.csv", CSV)
    conn = db(entry)
    totals = conn.execute(f'SELECT total FROM "{entry["table"]}" ORDER BY order_id').fetchall()
    assert totals == [(120.5,), (70.0,), (None,)]
    assert conn.execute(f'SELECT SUM(total) FROM "{entry["table"]}"').fetchone()[0] == 190.5
    conn.close()


def test_ingested_dataset_is_registered_and_answerable(uploads, monkeypatch):
    """The whole point: an upload goes through the identical contract pipeline."""
    entry = ingest.build_dataset("sales.csv", CSV)
    registry_file = uploads / "databases.json"
    registry_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(orch, "DATABASES_FILE", registry_file)

    registry = orch.load_registry()
    assert entry["id"] in registry
    assert Path(registry[entry["id"]]["path"]).is_file()

    reply = sql_reply(f'SELECT region, SUM(total) AS revenue FROM "{entry["table"]}" '
                      "GROUP BY region ORDER BY revenue DESC")
    calls = []

    def fake(messages, max_tokens=None):
        calls.append(messages)
        return json.dumps(reply if len(calls) == 1 else "West leads at 120.5.")

    monkeypatch.setattr(orch.agents.llm_client, "chat_completion", fake)
    result = orch.run_analysis("Total by region?", {"database": entry["id"]})
    assert result["status"] == "success"
    assert result["verification"]["passed"] is True
    assert result["rows"][0] == ["West", 120.5]
    assert result["chart"] == {"type": "bar", "x": "region", "y": "revenue"}


@pytest.fixture()
def server(uploads):
    """The uploads fixture keeps the route from writing into the real uploads dir."""
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from gateway.server import GatewayHandler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def post_json(base, path, payload, expect=200, user=None):
    import urllib.error
    import urllib.request

    headers = {"Content-Type": "application/json"}
    if user:
        headers["X-AskBase-User"] = user
    request = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            assert response.status == expect
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        assert exc.code == expect, exc.read()
        return json.loads(exc.read())


def get_json(base, path):
    import urllib.request

    with urllib.request.urlopen(base + path, timeout=30) as response:
        return json.loads(response.read())


def test_upload_route_registers_and_describes_the_dataset(server, uploads):
    body = post_json(server, "/api/datasets/upload", {"fileName": "sales.csv", "csv": CSV},
                     expect=201, user="admin")
    assert body["status"] == "ok"
    assert body["database"]["row_count"] == 3
    assert body["schema"]["tables"][0]["name"] == body["database"]["table"]
    assert "path" not in body["database"]              # no server filesystem paths leaked
    assert str(uploads) not in json.dumps(body)

    listed = get_json(server, "/api/databases")["databases"]
    uploaded = [entry for entry in listed if entry["id"] == body["database"]["id"]]
    assert uploaded and uploaded[0]["available"] is True


def test_upload_route_is_refused_for_a_viewer(server):
    """Registering a dataset changes what the server will open, so it is a write."""
    body = post_json(server, "/api/datasets/upload", {"fileName": "sales.csv", "csv": CSV},
                     expect=403, user="lea")
    assert body["status"] == "error"
    assert body["role"] == "viewer"
    assert "may not register" in body["message"]


def test_upload_route_rejects_an_unusable_csv(server):
    body = post_json(server, "/api/datasets/upload",
                     {"fileName": "empty.csv", "csv": "a,b,c\n"}, expect=400, user="admin")
    assert body["status"] == "error"
    assert "header row" in body["message"]


def test_analyze_route_answers_from_an_uploaded_dataset(server, monkeypatch):
    uploaded = post_json(server, "/api/datasets/upload",
                         {"fileName": "sales.csv", "csv": CSV}, expect=201,
                         user="admin")["database"]
    reply = sql_reply(f'SELECT region, SUM(total) AS revenue FROM "{uploaded["table"]}" '
                      "GROUP BY region ORDER BY revenue DESC")
    calls = []

    def fake(messages, max_tokens=None):
        calls.append(messages)
        return json.dumps(reply if len(calls) == 1 else "West leads at 120.5.")

    monkeypatch.setattr(orch.agents.llm_client, "chat_completion", fake)
    result = post_json(server, "/api/analyze",
                       {"question": "Total by region?",
                        "databaseConfig": {"database": uploaded["id"]}})
    assert result["status"] == "success"
    assert result["meta"]["code"] == "success"
    assert result["rows"] == [["West", 120.5], ["East", 70.0]]

