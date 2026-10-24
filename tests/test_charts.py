"""Matplotlib chart service tests: validation, ticket cache, PNG output, HTTP route."""
import json
import urllib.request

import pytest

from gateway import charts
from gateway.server import GatewayHandler

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(autouse=True)
def _clean_cache():
    charts._cache.clear()
    yield
    charts._cache.clear()


def _ticket(columns, rows, chart=None):
    return charts.cache_result(columns, rows, chart)


def test_cache_evicts_oldest_beyond_limit(monkeypatch):
    monkeypatch.setattr(charts, "MAX_TICKETS", 3)
    tickets = [_ticket(["a", "b"], [[1, 2]], None) for _ in range(5)]
    assert tickets[0] not in charts._cache and tickets[-1] in charts._cache


def test_unknown_ticket_is_404():
    with pytest.raises(charts.ChartError) as exc:
        charts.get_cached("nope")
    assert exc.value.status == 404


def test_unsupported_chart_type_rejected():
    cols, rows = ["region", "revenue"], [["West", 5.0], ["East", -2.0]]
    with pytest.raises(charts.ChartError, match="unsupported chart type"):
        charts.validate_spec({"type": "radar", "x": "region", "y": "revenue"}, cols, rows)


def test_columns_must_exist_in_result():
    cols, rows = ["region", "revenue"], [["West", 5.0]]
    with pytest.raises(charts.ChartError, match="not in the query result"):
        charts.validate_spec({"type": "bar", "x": "sneaky", "y": "revenue"}, cols, rows)
    with pytest.raises(charts.ChartError, match="not in the query result"):
        charts.validate_spec({"type": "bar", "x": "region", "y": "ghost"}, cols, rows)


def test_row_limit_rejects_large_results():
    cols = ["x", "y"]
    rows = [[i, float(i)] for i in range(charts.MAX_CHART_ROWS + 1)]
    with pytest.raises(charts.ChartError, match="chart limit"):
        charts.validate_spec({"type": "scatter", "x": "x", "y": "y"}, cols, rows)


def test_non_numeric_y_is_rejected_not_fabricated():
    cols, rows = ["region", "note"], [["West", "hello"], ["East", "world"]]
    with pytest.raises(charts.ChartError, match="no numeric values"):
        charts.render_png("bar", cols, rows, 0, 1)


@pytest.mark.parametrize("ctype", charts.CHART_TYPES)
def test_every_supported_type_renders_a_real_png(ctype):
    cols = ["region", "revenue"]
    rows = [["West", 12.0], ["East", -4.5], ["North", 7.0], ["South", None],
            ["East", 3.0], ["West", 9.5]]
    if ctype == "scatter":
        cols = ["discount", "profit"]
        rows = [[0.1, 12.0], [0.4, -4.5], [None, 7.0], [0.0, None], [0.2, 3.0]]
    if ctype == "pie":
        rows = [row for row in rows if row[1] is None or row[1] >= 0]
    png = charts.render_png(ctype, cols, rows, 0, 1)
    assert png.startswith(PNG_MAGIC)
    assert len(png) > 1000  # a real figure, not an empty buffer


def test_nulls_drop_pairs_instead_of_becoming_zero():
    xs, ys = charts._column_pairs([["a", None], ["b", 2.0], ["c", 5]], 0, 1)
    assert xs == ["b", "c"] and ys == [2.0, 5.0]
    assert charts._numeric(None) is None and charts._numeric(True) is None
    assert charts._numeric(float("nan")) is None
    assert charts._numeric(-3.5) == -3.5  # negatives are plottable


def test_histogram_and_box_need_no_category_column():
    cols = ["score"]
    rows = [[float(i % 7)] for i in range(30)]
    assert charts.render_png("histogram", cols, rows, 0, 0).startswith(PNG_MAGIC)
    assert charts.render_png("box", cols, rows, 0, 0).startswith(PNG_MAGIC)


def test_zero_row_result_refuses():
    with pytest.raises(charts.ChartError, match="zero rows"):
        charts.render_png("bar", ["a", "b"], [], 0, 1)


def test_pie_refuses_negative_values():
    with pytest.raises(charts.ChartError, match="cannot show negative"):
        charts.render_png("pie", ["cat", "profit"], [["A", 5.0], ["B", -2.0]], 0, 1)


def test_pie_groups_long_tail_into_other_slice():
    rows = [[f"s{i}", float(i + 1)] for i in range(20)]
    assert charts.render_png("pie", ["state", "sales"], rows, 0, 1).startswith(PNG_MAGIC)


@pytest.mark.parametrize("question,kind,cleaned", [
    ("Show me a pie chart of sales by region", "pie", "show sales by region"),
    ("total profit by category as a bar chart", "bar", "total profit by category"),
    ("Create a line graph of yearly sales", "line", "show yearly sales"),
    ("Plot monthly sales in 2016", "auto", "show monthly sales in 2016"),
    ("draw a donut chart showing share of sales per segment", "pie",
     "show share of sales per segment"),
    ("visualize revenue by state", "auto", "show revenue by state"),
])
def test_prompt_chart_requests_are_detected_and_stripped(question, kind, cleaned):
    assert charts.detect_chart_request(question) == (kind, cleaned)


@pytest.mark.parametrize("question", [
    "What is the sales trend by year?",
    "distribution of orders by ship mode",
    "Which region has the highest sales?",
])
def test_plain_data_questions_are_not_chart_requests(question):
    assert charts.detect_chart_request(question) == (None, question)


def test_spread_charts_ask_the_model_for_records_not_totals():
    kind, cleaned = charts.detect_chart_request("box plot of sales by segment")
    assert (kind, cleaned) == ("box", "show sales and segment for each individual record")


def test_requested_histogram_never_plots_an_id_column():
    spec, note = charts.spec_for_request(
        "histogram", ["row_id", "order_id", "sales"],
        [[1, 10, 5.0], [2, 11, 7.5], [3, 12, -1.0]])
    assert note is None and spec["y"] == "sales"


def test_requested_pie_with_negatives_falls_back_to_bar_and_says_so():
    spec, note = charts.spec_for_request("pie", ["category", "profit"],
                                         [["Tech", 10.0], ["Furniture", -3.0]])
    assert spec == {"type": "bar", "x": "category", "y": "profit"}
    assert "Pie chart not drawn" in note


def test_requested_chart_without_numeric_column_is_refused():
    spec, note = charts.spec_for_request("pie", ["a", "b"], [["x", "y"], ["z", "w"]])
    assert spec is None and "no numeric column" in note


def test_requested_scatter_needs_two_numeric_columns():
    spec, note = charts.spec_for_request("scatter", ["region", "sales"],
                                         [["W", 1.0], ["E", 2.0]])
    assert spec is None and "two numeric columns" in note


def test_requested_pie_picks_label_and_value_columns():
    spec, note = charts.spec_for_request(
        "pie", ["region", "orders", "sales"], [["W", 3, 10.0], ["E", 4, 20.0]])
    assert note is None and spec == {"type": "pie", "x": "region", "y": "orders"}


def test_envelope_honours_requested_chart(monkeypatch):
    import time as _time
    from gateway import orchestrator as orch
    env = orch._envelope(
        status="success", answer="a", sql="SELECT 1",
        result={"columns": ["region", "sales"], "rows": [["W", 5.0], ["E", 7.0]]},
        synthesis=None, gen={}, verification={"passed": True, "message": ""},
        error=None, warnings=[], started=_time.monotonic(), database="x",
        requested_chart="pie")
    assert env["chart"] == {"type": "pie", "x": "region", "y": "sales"}
    assert env["chartTicket"] in charts._cache


def test_handle_request_uses_only_cached_rows():
    ticket = _ticket(["region", "revenue"], [["West", 3.0], ["East", None], ["North", 8.0]],
                     {"type": "bar", "x": "region", "y": "revenue"})
    status, media, body = charts.handle_chart_request({"ticket": ticket})
    assert (status, media) == (200, "image/png") and body.startswith(PNG_MAGIC)
    # an explicit type over the same ticket works; a client-supplied rows field is ignored
    status, _, body = charts.handle_chart_request(
        {"ticket": ticket, "type": "box", "x": "region", "y": "revenue",
         "rows": [["fake", 999]]})
    assert status == 200 and body.startswith(PNG_MAGIC)
    # scatter needs a numeric x — a text column is refused, never coerced
    with pytest.raises(charts.ChartError, match="no numeric values"):
        charts.handle_chart_request({"ticket": ticket, "type": "scatter",
                                     "x": "region", "y": "revenue"})


def _http_error_body(exc: urllib.error.HTTPError) -> dict:
    with exc:
        return json.loads(exc.read())


import threading
from http.server import ThreadingHTTPServer


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_chart_route_returns_png(server):
    ticket = _ticket(["region", "revenue"],
                     [["West", -2.0], ["East", 4.0], ["West", 6.0]],
                     {"type": "bar", "x": "region", "y": "revenue"})
    with urllib.request.urlopen(f"{server}/api/chart?ticket={ticket}", timeout=15) as response:
        assert response.headers["Content-Type"] == "image/png"
        assert response.read().startswith(PNG_MAGIC)


def test_chart_route_unknown_ticket_is_404_json(server):
    try:
        urllib.request.urlopen(f"{server}/api/chart?ticket=deadbeef", timeout=15)
        pytest.fail("expected 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
        assert _http_error_body(exc)["status"] == "error"


def test_chart_route_bad_type_is_400(server):
    ticket = _ticket(["region", "revenue"], [["West", 1.0], ["East", 2.0]], None)
    try:
        urllib.request.urlopen(f"{server}/api/chart?ticket={ticket}&type=radar&x=region&y=revenue",
                               timeout=15)
        pytest.fail("expected 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        assert "unsupported chart type" in _http_error_body(exc)["message"]
