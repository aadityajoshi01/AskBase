"""One-click sales report — assembled only from verified, permission-filtered rows.

Every section follows the same path as an analysis answer (INTEGRATION_CONTRACT
§5.5): schema-detected SQL -> Agent 3 validate_sql -> role policy rewrite ->
Agent 3 execute_query -> Agent 3 verify_result. A section whose verification
fails is reported as unavailable, never quietly shown. Nothing here asks the
LLM anything: report numbers come straight from the database, so an offline
model cannot change a total.

Column detection is dynamic (the report runs against any registered SQLite
dataset): each section names the columns it needs and is skipped with a visible
reason when they are absent or hidden from the caller's role. Missing metrics
are never invented or defaulted to zero.

Charts are matplotlib PNGs (gateway.charts) rendered from the restricted rows
and embedded as base64, so the HTML is self-contained and browser-printable
("Save as PDF" is window.print() with a print stylesheet).
"""
from __future__ import annotations

import base64
import html as html_escape
import re
import time

from gateway import charts
from gateway.permissions import PolicyError, policy_note, restrict_query

_DATE_NAME = re.compile(r"(date|day|created|timestamp|time)", re.IGNORECASE)
_AMOUNT_NAME = re.compile(r"(total|amount|revenue|sales|subtotal)", re.IGNORECASE)
_PRICE_NAME = re.compile(r"(price)", re.IGNORECASE)
_QTY_NAME = re.compile(r"(quantity|qty|units)", re.IGNORECASE)
_CATEGORY_NAME = re.compile(r"^(category|genre|segment|type)$", re.IGNORECASE)
_PRODUCTISH = re.compile(r"(product|item)", re.IGNORECASE)

# SQLite has no date type; text dates like '20/02/2024' would make substr()
# month labels nonsense, so only ISO-style dates are aggregated and the rest
# are reported as a data-quality warning, never silently charted.
ISO_DATE_GLOB = "GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]*'"
_REGION_PRIORITY = ("region", "territory", "province", "state")


def _iso_month_ok(column: str) -> str:
    return f"{column} {ISO_DATE_GLOB} AND substr({column}, 6, 2) BETWEEN '01' AND '12'"


def _region_column(columns: dict) -> str | None:
    lowered = {name.lower(): name for name in columns}
    return next((lowered[w] for w in _REGION_PRIORITY if w in lowered), None)


def _q(identifier) -> str:
    return '"' + str(identifier).replace('"', "") + '"'


def _tables(schema: dict) -> dict[str, dict]:
    return {str(t.get("name", "")): t for t in schema.get("tables", [])}


def _columns(table: dict) -> dict[str, dict]:
    return {str(c.get("name", "")): c for c in table.get("columns", [])}


def _numeric(column: dict | None) -> bool:
    type_name = str((column or {}).get("type", "")).upper()
    return any(kind in type_name for kind in ("INT", "NUM", "REAL", "DEC", "FLOAT", "DOUBLE"))


def _first(columns: dict[str, dict], pattern, *, numeric_only: bool) -> str | None:
    for name, column in columns.items():
        if pattern.search(name) and (not numeric_only or _numeric(column)):
            return name
    return None


def _fks(table: dict) -> list[dict]:
    return [fk for fk in (table.get("foreign_keys") or []) if isinstance(fk, dict)]


def _pk(table: dict, fallback: str | None = None) -> str:
    return next((c for c, d in _columns(table).items() if d.get("pk")), None) or fallback or "id"


def detect_metrics(visible: dict) -> dict:
    """Pick the orders/items/products/region building blocks from a visible schema.

    Money either sits on the dated orders table ("order" grain) or on a line table
    that references a dated orders table ("line" grain, e.g. line_items.sales)."""
    tables = _tables(visible)
    found: dict = {"orders": None}

    for name, table in tables.items():
        columns = _columns(table)
        date_col = _first(columns, _DATE_NAME, numeric_only=False)
        amount_col = _first(columns, _AMOUNT_NAME, numeric_only=True)
        if date_col and amount_col:
            found.update({"orders": name, "date": date_col, "amount": amount_col,
                          "grain": "order", "orders_pk": _pk(table)})
            break

    if not found["orders"]:
        for name, table in tables.items():
            amount_col = _first(_columns(table), _AMOUNT_NAME, numeric_only=True)
            if not amount_col:
                continue
            for fk in _fks(table):
                target = tables.get(fk.get("references_table"))
                date_col = target and _first(_columns(target), _DATE_NAME, numeric_only=False)
                if date_col:
                    found.update({"orders": fk["references_table"], "date": date_col,
                                  "amount": amount_col, "grain": "line", "lines": name,
                                  "lines_to_orders": fk["column"],
                                  "orders_pk": fk.get("references_column") or _pk(target)})
                    break
            if found["orders"]:
                break

    if not found["orders"]:
        return found

    candidates = [found["lines"]] if found["grain"] == "line" else list(tables)
    for name in candidates:
        if name == found["orders"]:
            continue
        table = tables[name]
        columns = _columns(table)
        qty = _first(columns, _QTY_NAME, numeric_only=True)
        to_orders = next((f for f in _fks(table)
                          if f.get("references_table") == found["orders"]), None)
        to_product = next((f for f in _fks(table)
                           if f.get("references_table") in tables
                           and _PRODUCTISH.search(str(f.get("column") or ""))
                           and f["references_table"] != found["orders"]), None)
        if to_orders and to_product and (qty or found["grain"] == "line"):
            product_table = tables[to_product["references_table"]]
            names = list(_columns(product_table))
            pname = (next((c for c in names if c.lower() == "name" or c.lower().endswith("_name")), None)
                     or next((c for c in names if _PRODUCTISH.search(c)
                              and not c.lower().endswith("id")), None))
            found.update({"items": name, "qty": qty,
                          "price": _first(columns, _PRICE_NAME, numeric_only=True),
                          "items_to_product": to_product["column"],
                          "products": to_product["references_table"],
                          "product_name": pname,
                          "product_pk": to_product.get("references_column") or _pk(product_table)})
            break

    orders_table = tables[found["orders"]]
    for name, table in tables.items():
        region = _region_column(_columns(table))
        if not region:
            continue
        if name == found["orders"]:
            found.update({"region_table": name, "region_col": region, "region_join": "direct"})
            break
        fk_from_orders = next((f for f in _fks(orders_table)
                               if f.get("references_table") == name), None)
        if fk_from_orders:
            found.update({"region_table": name, "region_col": region,
                          "region_join": "orders_fk",
                          "region_fk_column": fk_from_orders["column"],
                          "region_pk": fk_from_orders.get("references_column") or _pk(table)})
            break

    found["category_col"] = (_first(_columns(tables[found["products"]]), _CATEGORY_NAME,
                                    numeric_only=False) if found.get("products") else None)
    return found


def build_sections(m: dict) -> tuple[list[dict], list[dict]]:
    """(specs, pre-skipped) — plans built only from detected columns; each still goes
    through validate -> permission rewrite -> execute -> verify."""
    specs: list[dict] = []
    skipped: list[dict] = []
    if not m.get("orders"):
        return specs, [{"title": "All sections",
                        "note": "no dated orders table with a money column (on it or its line items)"}]
    o, d = _q(m["orders"]), _q(m["date"])
    if m["grain"] == "line":
        amt = f"l.{_q(m['amount'])}"
        fact = (f"FROM {_q(m['lines'])} l JOIN {o} o "
                f"ON o.{_q(m['orders_pk'])} = l.{_q(m['lines_to_orders'])}")
        orders_count = f"COUNT(DISTINCT l.{_q(m['lines_to_orders'])})"
        money_ref = f"{m['lines']}.{m['amount']}"
    else:
        amt, fact, orders_count = f"o.{_q(m['amount'])}", f"FROM {o} o", "COUNT(*)"
        money_ref = f"{m['orders']}.{m['amount']}"

    specs.append({
        "id": "kpis", "title": "Key numbers", "requires": [money_ref],
        "sql": (f"SELECT {orders_count} AS orders, ROUND(SUM({amt}), 2) AS total_revenue, "
                f"ROUND(SUM({amt}) * 1.0 / {orders_count}, 2) AS avg_order_value "
                f"{fact} WHERE {amt} IS NOT NULL"),
        "chart": None})
    specs.append({
        "id": "monthly_revenue", "title": "Monthly revenue trend",
        "requires": [f"{m['orders']}.{m['date']}", money_ref],
        "sql": (f"SELECT substr(o.{d}, 1, 7) AS month, ROUND(SUM({amt}), 2) AS revenue "
                f"{fact} WHERE {amt} IS NOT NULL AND {_iso_month_ok(f'o.{d}')} "
                f"GROUP BY month ORDER BY month"),
        "chart": {"type": "line", "x": "month", "y": "revenue"}})
    specs.append({
        "id": "date_quality", "title": "Rows excluded from the monthly trend",
        "requires": [f"{m['orders']}.{m['date']}", money_ref],
        "sql": (f"SELECT COUNT(*) AS excluded_rows {fact} WHERE {amt} IS NOT NULL "
                f"AND NOT (o.{d} IS NOT NULL AND {_iso_month_ok(f'o.{d}')})"),
        "chart": None})

    if m.get("items") and m.get("product_name"):
        it, pr = _q(m["items"]), _q(m["products"])
        join = (f"FROM {it} it JOIN {pr} pr ON pr.{_q(m['product_pk'])} = "
                f"it.{_q(m['items_to_product'])}")
        if m["grain"] == "line":
            measure, measure_col = f"it.{_q(m['amount'])}", m["amount"]
            amount, label, title = f"ROUND(SUM({measure}), 2)", "revenue", "Top products by revenue"
        elif m.get("price"):
            measure, measure_col = f"it.{_q(m['qty'])}", m["qty"]
            amount = f"ROUND(SUM({measure} * it.{_q(m['price'])}), 2)"
            label, title = "revenue", "Top products by revenue"
        else:
            measure, measure_col = f"it.{_q(m['qty'])}", m["qty"]
            amount, label, title = f"ROUND(SUM({measure}), 2)", "units", "Top products by units sold"
        where = f"WHERE {measure} IS NOT NULL"
        specs.append({
            "id": "top_products", "title": title,
            "requires": [f"{m['items']}.{measure_col}", f"{m['products']}.{m['product_name']}"],
            "sql": (f"SELECT pr.{_q(m['product_name'])} AS product, {amount} AS {label} "
                    f"{join} {where} GROUP BY product ORDER BY {label} DESC LIMIT 8"),
            "chart": {"type": "bar", "x": "product", "y": label}})
        if m.get("category_col"):
            c = _q(m["category_col"])
            specs.append({
                "id": "category_comparison", "title": "Revenue by product category",
                "requires": [f"{m['products']}.{m['category_col']}"],
                "sql": (f"SELECT pr.{c} AS category, {amount} AS {label} {join} "
                        f"{where} AND pr.{c} IS NOT NULL "
                        f"GROUP BY category ORDER BY {label} DESC LIMIT 12"),
                "chart": {"type": "bar", "x": "category", "y": label}})
        else:
            skipped.append({"title": "Revenue by product category",
                            "note": "products table has no category/segment column you can see"})
    else:
        skipped.append({"title": "Top products / categories",
                        "note": "no item table linking orders to products in this schema"})

    if m.get("region_col"):
        if m.get("region_join") == "direct":
            region_expr, region_join = f"o.{_q(m['region_col'])}", ""
        else:
            region_expr = f"r.{_q(m['region_col'])}"
            region_join = (f" JOIN {_q(m['region_table'])} r "
                           f"ON r.{_q(m['region_pk'])} = o.{_q(m['region_fk_column'])}")
        specs.append({
            "id": "regional_comparison", "title": "Revenue by region",
            "requires": [f"{m['region_table']}.{m['region_col']}"],
            "sql": (f"SELECT {region_expr} AS region, ROUND(SUM({amt}), 2) AS revenue "
                    f"{fact}{region_join} WHERE {amt} IS NOT NULL AND {region_expr} IS NOT NULL "
                    f"GROUP BY region ORDER BY revenue DESC LIMIT 12"),
            "chart": {"type": "bar", "x": "region", "y": "revenue"}})
    else:
        skipped.append({"title": "Regional comparison",
                        "note": "no region/state/province column reachable from orders "
                                "(hidden from your access level, or absent)"})
    return specs, skipped


def _run_section(agents, section: dict, policy: dict, stored_schema: dict,
                 config: dict, dialect: str) -> dict:
    """validate -> permission rewrite -> execute -> verify; gate on passed only."""
    outcome = {**section, "status": "unavailable", "columns": [], "rows": [],
               "png_b64": "", "note": "", "restricted": False}
    try:
        validation = agents.validate_sql(section["sql"], dialect)
        if not validation.get("valid"):
            error = validation.get("error") or {}
            outcome["note"] = f"rejected by the safety validator: {error.get('message') or error.get('type') or 'invalid_sql'}"
            return outcome
        sql = section["sql"]
        if policy and not policy.get("unrestricted"):
            enforced = restrict_query(sql, policy, stored_schema, dialect)
            if enforced.get("changed"):
                sql = enforced["sql"]
                outcome["restricted"] = True
        result = agents.execute_query(sql, config)
        if result.get("status") != "success":
            error = result.get("error") or {}
            outcome["note"] = f"execution failed: {error.get('type') or 'unknown'}"
            return outcome
        verdict = agents.verify_result(section["title"], result)
        if not verdict.get("passed"):
            reasons = ", ".join(f.get("reason", "") for f in verdict.get("failures", []))
            outcome["note"] = f"result verification failed: {reasons or 'unverified'}"
            return outcome
        outcome["columns"] = list(result.get("columns") or [])
        outcome["rows"] = [list(r) for r in result.get("rows") or []]
        outcome["executed_sql"] = result.get("sql") or sql
        if result.get("truncated"):
            outcome["note"] = "rows truncated by the role's row limit"
        spec = section.get("chart")
        if spec:
            try:
                ctype, x, y = charts.validate_spec(dict(spec), outcome["columns"], outcome["rows"])
                png = charts.render_png(ctype, outcome["columns"], outcome["rows"], x, y,
                                        title=section["title"])
                outcome["png_b64"] = base64.b64encode(png).decode("ascii")
            except charts.ChartError as exc:
                outcome["note"] = "; ".join(filter(None, [outcome["note"], f"chart skipped: {exc}"]))
        outcome["status"] = "verified"
    except PolicyError as exc:
        outcome["note"] = f"blocked by permission policy: {exc}"
    except Exception as exc:  # one broken section must never take down the report
        outcome["note"] = f"section failed: {exc}"
    return outcome


def _fmt(value) -> str:
    if isinstance(value, bool) or value is None:
        return str(value if value is not None else "—")
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def build_findings(outcomes: list[dict]) -> list[str]:
    findings: list[str] = []
    for outcome in outcomes:
        chart = outcome.get("chart")
        if outcome["status"] != "verified" or not chart or not outcome["rows"]:
            continue
        lowered = [str(c).lower() for c in outcome["columns"]]
        xi = lowered.index(chart["x"].lower()) if chart["x"].lower() in lowered else 0
        yi = lowered.index(chart["y"].lower()) if chart["y"].lower() in lowered else -1
        if yi < 0:
            continue
        numeric = [r for r in outcome["rows"]
                   if len(r) > yi and isinstance(r[yi], (int, float)) and not isinstance(r[yi], bool)]
        if not numeric:
            continue
        top = max(numeric, key=lambda r: r[yi])
        total = sum(r[yi] for r in numeric)
        leader = f"{outcome['title']}: {_fmt(top[xi])} leads with {_fmt(top[yi])}"
        if total:
            leader += f" ({abs(top[yi]) / abs(total):.0%} of the shown total)"
        findings.append(leader)
    kpi = next((o for o in outcomes if o.get("id") == "kpis" and o["status"] == "verified"
                and o["rows"]), None)
    if kpi:
        columns = [str(c).lower() for c in kpi["columns"]]
        row = kpi["rows"][0]
        parts = []
        for name in ("total_revenue", "orders"):
            if name in columns:
                value = row[columns.index(name)]
                parts.append(f"{name.replace('_', ' ')} of {_fmt(value)}")
        if parts:
            findings.insert(0, "Overall: " + ", ".join(parts) + ".")
    return findings


def _kpi_cards(kpi_outcome: dict | None) -> str:
    if not kpi_outcome or kpi_outcome["status"] != "verified" or not kpi_outcome["rows"]:
        return '<div class="kpi"><span>KPIs</span><strong>unavailable</strong></div>'
    labels = {"orders": "Orders", "total_revenue": "Total revenue", "avg_order_value": "Avg order value"}
    cards = []
    for name, value in zip(kpi_outcome["columns"], kpi_outcome["rows"][0]):
        key = str(name).lower()
        cards.append(f'<div class="kpi"><span>{html_escape.escape(labels.get(key, str(name).replace("_", " ")))}'
                     f'</span><strong>{html_escape.escape(_fmt(value))}</strong></div>')
    return "\n".join(cards)


def build_html(*, database_label, role, generated_at, outcomes, skipped, warnings,
               findings, kpi_html) -> str:
    cards = []
    for outcome in outcomes:
        # the KPI numbers render as cards at the top; no duplicate section
        if outcome["status"] != "verified" or outcome.get("id") in ("kpis", "date_quality"):
            continue
        img = (f'<img alt="Chart: {html_escape.escape(outcome["title"])}" '
               f'src="data:image/png;base64,{outcome["png_b64"]}">'
               if outcome["png_b64"] else "")
        provenance = " · permission-filtered" if outcome.get("restricted") else ""
        rows_note = f"{len(outcome['rows'])} verified rows{provenance}"
        cards.append(f'<section class="card"><h2>{html_escape.escape(outcome["title"])}</h2>{img}'
                     f'<p class="note">{html_escape.escape(rows_note)}</p></section>')
    listify = lambda items, empty: "".join(items) or f"<li>{html_escape.escape(empty)}</li>"
    skipped_html = listify(
        [f'<li><strong>{html_escape.escape(s["title"])}</strong> — {html_escape.escape(s.get("note") or "unverified")}</li>'
         for s in skipped], "Everything this dataset supports is included above.")
    warnings_html = listify(
        [f"<li>{html_escape.escape(w)}</li>" for w in warnings], "None raised.")
    findings_html = listify(
        [f"<li>{html_escape.escape(f)}</li>" for f in findings], "No verified data to summarise.")
    charts_html = "\n".join(cards) or (
        '<section class="card"><h2>No verified sections</h2><p class="note">'
        'Every report section failed validation, execution or verification; '
        'nothing here shows unverified numbers.</p></section>')
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>AskBase sales report — {html_escape.escape(database_label)}</title>
<style>
 body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 2rem auto; max-width: 940px; color: #1c2126; }}
 h1 {{ margin-bottom: .2rem; }} .sub {{ color: #5b6670; margin-top: 0; }}
 .toolbar {{ position: sticky; top: 0; background: #fff; padding: .6rem 0; z-index: 2; }}
 .toolbar button {{ background: #1565c0; color: #fff; border: 0; border-radius: 999px;
   padding: .55rem 1.2rem; font-size: .95rem; cursor: pointer; }}
 .kpis {{ display: flex; gap: 1rem; flex-wrap: wrap; margin: 1rem 0 1.6rem; }}
 .kpi {{ border: 1px solid #dfe5ea; border-radius: 12px; padding: .8rem 1.1rem; min-width: 150px; }}
 .kpi span {{ display: block; color: #5b6670; font-size: .8rem; }}
 .kpi strong {{ font-size: 1.3rem; }}
 .card {{ border: 1px solid #dfe5ea; border-radius: 12px; padding: 1rem 1.2rem; margin-bottom: 1.2rem; }}
 .card img {{ max-width: 100%; }} .note {{ color: #5b6670; font-size: .85rem; }}
 footer {{ color: #5b6670; font-size: .8rem; border-top: 1px solid #dfe5ea; padding-top: .8rem; margin-top: 2rem; }}
 ul {{ line-height: 1.55; }}
 @media print {{ .toolbar {{ display: none; }} body {{ margin: 0; }} .card {{ break-inside: avoid; }} }}
</style></head><body>
<div class="toolbar"><button onclick="window.print()">Save as PDF</button></div>
<h1>Sales report</h1>
<p class="sub">{html_escape.escape(database_label)} · role '{html_escape.escape(str(role))}' · generated {html_escape.escape(generated_at)}</p>
<div class="kpis">{kpi_html}</div>
{charts_html}
<section class="card"><h2>Key findings</h2><ul>{findings_html}</ul></section>
<section class="card"><h2>Data-quality warnings</h2><ul>{warnings_html}</ul></section>
<section class="card"><h2>Not included (missing or restricted)</h2><ul>{skipped_html}</ul></section>
<footer>All figures come from read-only SQL that passed the safety validator and result
verification. Sections that failed any gate are listed above, never charted.</footer>
</body></html>"""


def build_report(agents, *, entry: dict, policy: dict, stored_schema: dict,
                 visible_schema: dict, hidden_columns: list[str], database_id: str,
                 started: float | None = None) -> dict:
    started = started or time.monotonic()
    dialect = entry.get("dialect", visible_schema.get("dialect", "sqlite"))
    config = {"type": "sqlite", "dialect": dialect, "path": entry["path"]}
    for key in ("max_rows", "timeout_s"):
        if key in entry:
            config[key] = entry[key]
    role_max = policy.get("max_rows")
    if not policy.get("unrestricted") and isinstance(role_max, int) and role_max >= 1:
        config["max_rows"] = min(int(config.get("max_rows", role_max)), role_max)

    metrics = detect_metrics(visible_schema)
    specs, skipped = build_sections(metrics)
    outcomes = [_run_section(agents, s, policy, stored_schema, config, dialect) for s in specs]
    for outcome in outcomes:
        if outcome["status"] != "verified":
            skipped.append({"title": outcome["title"], "note": outcome["note"] or "unverified"})
    verified = [o for o in outcomes if o["status"] == "verified"]

    warnings = list(dict.fromkeys(filter(None, [
        policy_note(policy, {}, hidden_columns),
        *(visible_schema.get("warnings") or []),
        *(entry.get("warnings") or []),
        *(f"{o['title']}: {o['note']}" for o in outcomes if o["status"] == "verified" and o["note"]),
    ])))
    dq = next((o for o in outcomes if o.get("id") == "date_quality"
               and o["status"] == "verified" and o["rows"]), None)
    if dq and dq["rows"][0][0]:
        warnings.append(f"{dq['rows'][0][0]} row(s) with a missing or invalid date were "
                        "left out of the monthly trend (they are still in the KPI totals).")
    warnings += [f"{s['title']} not included: {s['note']}" for s in skipped]

    kpi_outcome = next((o for o in outcomes if o.get("id") == "kpis"), None)
    html_doc = build_html(
        database_label=entry.get("label", database_id), role=policy["role"],
        generated_at=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        outcomes=outcomes, skipped=skipped, warnings=warnings,
        findings=build_findings(outcomes), kpi_html=_kpi_cards(kpi_outcome))
    return {
        "status": "success" if verified else "error",
        "message": "" if verified else "No report section survived verification.",
        "html": html_doc,
        "database": database_id,
        "sections": [{k: o.get(k) for k in
                      ("id", "title", "status", "note", "columns", "rows", "restricted")}
                     for o in outcomes],
        "skipped": skipped,
        "warnings": warnings,
        "findings": build_findings(outcomes),
        "meta": {"durationMs": int((time.monotonic() - started) * 1000), "readOnly": True,
                 "role": policy["role"], "sections_verified": len(verified),
                 "sections_total": len(outcomes), "hidden_columns": hidden_columns},
    }
