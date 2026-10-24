"""Agent 1 Part 9 — live smoke test. Run ONLY with the llamafile server up.

    cd ai-analyst
    ../.venv/Scripts/python.exe smoke_test.py

Probes /v1/models, then pushes three fixture questions through the REAL
generate_sql() path and one synthesize_answer() call, printing actual outputs
and wall-clock latencies. Nothing here is committed as a "result" until it
runs against the live model; failures are recorded as failures.
"""
import csv
import json
import sys
import time
from datetime import datetime, timezone

import requests

from backend import llm_client
from backend.agent import synthesize_answer
from backend.sql_generator import generate_sql

RESULTS_CSV = "smoke_results.csv"
CSV_FIELDS = ["timestamp", "run_id", "kind", "label", "question",
              "status", "latency_ms", "error_type"]

BASE_URL = llm_client.get_config()["base_url"]

SCHEMA = {
    "dialect": "sqlite",
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

QUESTIONS = [
    ("join+aggregation", "What is total order revenue by customer region?"),
    ("multi-table ranking", "Which product categories sold the most units overall?"),
    ("ambiguous - should clarify", "How have sales been doing recently?"),
]

FAKE_RESULT = {  # deterministic result rows for the synthesis probe
    "status": "success", "columns": ["region", "revenue"],
    "rows": [["West", 12500.5], ["East", 9840.0]], "row_count": 2,
    "truncated": False, "warnings": [], "verification": {"passed": True},
}


def probe_models():
    resp = requests.get(f"{BASE_URL}/models", timeout=10)
    resp.raise_for_status()
    ids = [m.get("id") for m in resp.json().get("data", [])]
    print(f"[models] endpoint={BASE_URL} model_ids={ids}")
    return ids


def log_row(run_id, kind, label, question, out, ms):
    """Append one measurement to RESULTS_CSV so before/after runs stay comparable."""
    error = out.get("error") or {}
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": run_id, "kind": kind, "label": label, "question": question,
        "status": out.get("status"), "latency_ms": ms,
        "error_type": error.get("type", ""),
    }
    try:
        with open(RESULTS_CSV, "a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            if fh.tell() == 0:
                writer.writeheader()
            writer.writerow(row)
    except OSError as exc:
        print(f"[warn] could not append to {RESULTS_CSV}: {exc}")


def main():
    run_id = datetime.now(timezone.utc).strftime("%Y%mdT%H%M%SZ")
    try:
        probe_models()
    except Exception as exc:
        print(f"[FAIL] server unreachable at {BASE_URL}: {exc}")
        sys.exit(1)

    results = []
    for label, question in QUESTIONS:
        t0 = time.perf_counter()
        out = generate_sql(question, SCHEMA)
        ms = int((time.perf_counter() - t0) * 1000)
        results.append((label, question, out, ms))
        log_row(run_id, "generate_sql", label, question, out, ms)
        print(f"\n[{label}] {question}\n  status={out['status']} ({ms} ms)")
        print(f"  sql={out['sql']}")
        if out["assumptions"]:
            print(f"  assumptions={out['assumptions']}")
        if out["clarification_question"]:
            print(f"  clarify={out['clarification_question']}")
        if out["error"]:
            print(f"  error={out['error']}")

    first_ok = next((r for r in results if r[2]["status"] == "ok"), None)
    if first_ok:
        t0 = time.perf_counter()
        syn = synthesize_answer(first_ok[1], first_ok[2]["sql"], FAKE_RESULT)
        ms = int((time.perf_counter() - t0) * 1000)
        log_row(run_id, "synthesize_answer", "synthesis-probe", first_ok[1], syn, ms)
        print(f"\n[synthesize] status={syn['status']} ({ms} ms)\n  answer={syn['answer']}")
    else:
        print("\n[synthesize] SKIPPED — no successful generation to synthesize")

    print("\n[json] " + json.dumps(
        [{"label": l, "question": q, "latency_ms": ms,
          "status": o["status"], "sql": o["sql"]} for l, q, o, ms in results],
        indent=2))


if __name__ == "__main__":
    main()
