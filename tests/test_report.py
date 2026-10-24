"""Generate Report — real schema discovery, Agent 3 validate/execute/verify and the
permission rewrite; no LLM is involved. Expected numbers are hand-computed."""
import base64
import json
import sqlite3
from pathlib import Path

import pytest

import gateway.orchestrator as orch
from gateway import report


SALES_SQL = """
CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, name TEXT, region TEXT);
CREATE TABLE products (id INTEGER PRIMARY KEY, product_name TEXT, category TEXT);
CREATE TABLE orders (order_id INTEGER PRIMARY KEY,
                     customer_id INTEGER REFERENCES customers(customer_id),
                     order_date DATE, total REAL);
CREATE TABLE order_items (id INTEGER PRIMARY KEY,
                          order_id INTEGER REFERENCES orders(order_id),
                          product_id INTEGER REFERENCES products(id),
                          quantity INTEGER, unit_price REAL);
INSERT INTO customers VALUES (1,'Ana','West'), (2,'Bo','East');
INSERT INTO products VALUES (1,'Widget','Tools'), (2,'Gadget','Toys');
INSERT INTO orders VALUES (1,1,'2024-01-05',30.0), (2,2,'2024-01-20',10.0),
                          (3,1,'2024-02-03',50.0);
INSERT INTO order_items VALUES (1,1,1,2,10.0), (2,1,2,2,5.0),
                               (3,2,2,2,5.0), (4,3,1,5,10.0);
"""
# KPIs: 3 orders, revenue 90.00, AOV 30.00. Months: 2024-01=40, 2024-02=50.
# Products: Widget 2*10+5*10=70, Gadget 2*5+2*5=20. Regions: West 80, East 10.

POLICIES = {
    "role_defaults": {"admin": {"unrestricted": True},
                      "viewer": {"max_rows": 200}},
    "users": {"admin": {"role": "admin"}, "vic": {"role": "viewer"},
              "mo": {"role": "viewer"}},
    "datasets": {"demo": {"viewer": {
        "row_filters": {
            "customers": {"column": "region", "in": ["East"]},
            "orders": {"semi_join": {"column": "customer_id", "from_table": "customers",
                                     "where": {"column": "region", "in": ["East"]}}},
            "order_items": {"semi_join": {
                "column": "order_id", "from_table": "orders",
                "where": {"semi_join": {"column": "customer_id", "from_table": "customers",
                                        "where": {"column": "region", "in": ["East"]}}}}},
        }}}},
}


def _register(monkeypatch, path, policies=POLICIES):
    entry = {"id": "demo", "label": "Demo", "type": "sqlite", "path": str(path),
             "build": "test fixture"}
    monkeypatch.setattr(orch, "load_registry", lambda: {"demo": entry})
    monkeypatch.setattr(orch, "load_policies", lambda *a, **k: policies)
    return entry


def _db(tmp_path: Path, script: str) -> Path:
    path = tmp_path / "sales.db"
    conn = sqlite3.connect(path)
    conn.executescript(script)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def sales(tmp_path, monkeypatch):
    return _register(monkeypatch, _db(tmp_path, SALES_SQL))


def _section(result, section_id):
    return next(s for s in result["sections"] if s["id"] == section_id)


def _as_map(section):
    return {row[0]: row[1] for row in section["rows"]}


# --- calculations -----------------------------------------------------------

def test_kpis_and_trends_match_hand_computed_values(sales):
    result = orch.run_report({"database": "demo"}, "admin")
    assert result["status"] == "success"
    kpis = _section(result, "kpis")
    assert kpis["status"] == "verified"
    assert kpis["rows"] == [[3, 90.0, 30.0]]
    assert _as_map(_section(result, "monthly_revenue")) == {"2024-01": 40.0, "2024-02": 50.0}
    top = _section(result, "top_products")
    assert top["columns"] == ["product", "revenue"]
    assert top["rows"] == [["Widget", 70.0], ["Gadget", 20.0]]
    assert _as_map(_section(result, "category_comparison")) == {"Tools": 70.0, "Toys": 20.0}
    assert _as_map(_section(result, "regional_comparison")) == {"West": 80.0, "East": 10.0}
    assert result["meta"]["sections_verified"] == result["meta"]["sections_total"] == 6
    assert _section(result, "date_quality")["rows"] == [[0]]
    assert result["meta"]["readOnly"] is True


def test_html_is_printable_and_charts_are_real_pngs(sales):
    result = orch.run_report({"database": "demo"}, "admin")
    html = result["html"]
    assert "window.print()" in html and "@media print" in html
    assert html.count("data:image/png;base64,") == 4          # kpis carry no chart
    for chunk in html.split("data:image/png;base64,")[1:]:
        assert base64.b64decode(chunk.split('"', 1)[0])[:8] == b"\x89PNG\r\n\x1a\n"
    assert any("Widget" in f for f in result["findings"])


def test_line_grain_sales_schema_superstore_shape(tmp_path, monkeypatch):
    _register(monkeypatch, _db(tmp_path, """
        CREATE TABLE customers (customer_id TEXT, region TEXT);
        CREATE TABLE products (product_id TEXT, name TEXT, category TEXT);
        CREATE TABLE orders (order_id TEXT, order_date DATE,
                             customer_id TEXT REFERENCES customers(customer_id));
        CREATE TABLE line_items (row_id INTEGER, order_id TEXT REFERENCES orders(order_id),
                                 product_id TEXT REFERENCES products(product_id), sales REAL);
        INSERT INTO customers VALUES ('C1','West'), ('C2','East');
        INSERT INTO products VALUES ('P1','Chair','Furniture'), ('P2','Pen','Office');
        INSERT INTO orders VALUES ('O1','2024-01-03','C1'), ('O2','2024-02-10','C2');
        INSERT INTO line_items VALUES (1,'O1','P1',100.0), (2,'O1','P2',5.0),
                                      (3,'O2','P2',15.0);"""))
    result = orch.run_report({"database": "demo"}, "admin")
    assert _section(result, "kpis")["rows"] == [[2, 120.0, 60.0]]
    assert _as_map(_section(result, "monthly_revenue")) == {"2024-01": 105.0, "2024-02": 15.0}
    assert _section(result, "top_products")["rows"] == [["Chair", 100.0], ["Pen", 20.0]]
    assert _as_map(_section(result, "category_comparison")) == {"Furniture": 100.0, "Office": 20.0}
    assert _as_map(_section(result, "regional_comparison")) == {"West": 105.0, "East": 15.0}


# --- missing columns --------------------------------------------------------

def test_missing_region_and_items_are_skipped_not_invented(tmp_path, monkeypatch):
    _register(monkeypatch, _db(tmp_path, """
        CREATE TABLE orders (id INTEGER PRIMARY KEY, order_date DATE, total REAL);
        INSERT INTO orders VALUES (1,'2024-03-01',12.5), (2,'2024-03-09',7.5);"""))
    result = orch.run_report({"database": "demo"}, "admin")
    assert [s["id"] for s in result["sections"]] == ["kpis", "monthly_revenue", "date_quality"]
    assert _section(result, "kpis")["rows"] == [[2, 20.0, 10.0]]
    skipped = " ".join(s["title"] for s in result["skipped"]).lower()
    assert "product" in skipped and "region" in skipped and "categor" in skipped
    assert "Not included" in result["html"]


def test_no_date_and_amount_table_means_no_report(tmp_path, monkeypatch):
    _register(monkeypatch, _db(tmp_path, """
        CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);
        INSERT INTO notes VALUES (1,'hello');"""))
    result = orch.run_report({"database": "demo"}, "admin")
    assert result["status"] == "error"
    assert result["sections"] == []
    assert result["skipped"]


def test_non_iso_dates_are_excluded_from_the_trend(tmp_path, monkeypatch):
    _register(monkeypatch, _db(tmp_path, """
        CREATE TABLE orders (id INTEGER PRIMARY KEY, order_date TEXT, total REAL);
        INSERT INTO orders VALUES (1,'2024-03-01',10.0), (2,'20/03/2024',99.0),
                                  (3,'2024-13-45',5.0), (4,NULL,1.0);"""))
    result = orch.run_report({"database": "demo"}, "admin")
    assert _as_map(_section(result, "monthly_revenue")) == {"2024-03": 10.0}
    assert _section(result, "kpis")["rows"] == [[4, 115.0, 28.75]]
    assert _section(result, "date_quality")["rows"] == [[3]]
    assert any(w.startswith("3 row(s) with a missing or invalid date") for w in result["warnings"])


# --- incorrect results ------------------------------------------------------

def test_failed_verification_drops_the_section(sales, monkeypatch):
    real = orch.agents.verify_result

    def verify(question, result):
        if "Top products" in question:
            return {"passed": False, "failures": [{"check": "x", "reason": "row_count_mismatch",
                                                   "message": "forged"}]}
        return real(question, result)

    monkeypatch.setattr(orch.agents, "verify_result", verify)
    result = orch.run_report({"database": "demo"}, "admin")
    top = _section(result, "top_products")
    assert top["status"] == "unavailable" and top["rows"] == []
    assert "row_count_mismatch" in top["note"]
    assert result["meta"]["sections_verified"] == 5
    assert result["html"].count("data:image/png;base64,") == 3
    assert not any("Widget" in f for f in result["findings"])


def test_validator_rejection_and_execution_error_are_never_rendered(sales, monkeypatch):
    monkeypatch.setattr(orch.agents, "validate_sql",
                        lambda sql, dialect="sqlite": {"valid": False,
                                                       "error": {"type": "unsafe", "message": "nope"}})
    result = orch.run_report({"database": "demo"}, "admin")
    assert result["status"] == "error"
    assert all(s["status"] == "unavailable" and not s["rows"] for s in result["sections"])
    assert "data:image/png" not in result["html"]


def test_invalid_chart_spec_keeps_data_but_skips_chart(sales, monkeypatch):
    original = report.build_sections

    def broken(metrics):
        specs, skipped = original(metrics)
        for spec in specs:
            if spec["id"] == "monthly_revenue":
                spec["chart"] = {"type": "pie3d", "x": "month", "y": "revenue"}
        return specs, skipped

    monkeypatch.setattr(report, "build_sections", broken)
    result = orch.run_report({"database": "demo"}, "admin")
    monthly = _section(result, "monthly_revenue")
    assert monthly["status"] == "verified"
    assert "chart skipped" in monthly["note"]
    assert result["html"].count("data:image/png;base64,") == 3


# --- permissions ------------------------------------------------------------

def test_viewer_row_filter_restricts_every_number(sales):
    result = orch.run_report({"database": "demo"}, "vic")
    assert result["meta"]["role"] == "viewer"
    kpis = _section(result, "kpis")
    assert kpis["restricted"] is True
    assert kpis["rows"] == [[1, 10.0, 10.0]]
    assert _as_map(_section(result, "regional_comparison")) == {"East": 10.0}
    assert _as_map(_section(result, "monthly_revenue")) == {"2024-01": 10.0}
    assert _section(result, "top_products")["rows"] == [["Gadget", 10.0]]
    assert "West" not in result["html"] and "Widget" not in result["html"]


def test_masked_region_column_removes_regional_section(tmp_path, monkeypatch):
    policies = json.loads(json.dumps(POLICIES))
    policies["datasets"]["demo"]["viewer"] = {"masked_columns": ["customers.region"]}
    _register(monkeypatch, _db(tmp_path, SALES_SQL), policies)
    result = orch.run_report({"database": "demo"}, "mo")
    assert "regional_comparison" not in [s["id"] for s in result["sections"]]
    assert "West" not in result["html"] and "East" not in result["html"]
    assert "customers.region" in result["meta"]["hidden_columns"]


def test_unknown_database_raises(sales):
    with pytest.raises(orch.UnknownDatabase):
        orch.run_report({"database": "nope"}, "admin")
