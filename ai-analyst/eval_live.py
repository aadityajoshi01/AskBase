"""Agent 1 live evaluation — accuracy + latency against the real Qwen3 server.

    cd ai-analyst
    ../.venv/Scripts/python.exe eval_live.py [--repeats N] [--dataset NAME]

Datasets: `fixture` (default — hand-written mini schema from smoke_test.py),
`superstore`, `cmaps` (see datasets/question_sets.py for labeled questions +
ground truths). Add --dataset to run the Kaggle-backed sets once their .db
files exist (datasets/build_datasets.py).

Metrics are STATIC checks only (identifier guard, single-SELECT shape, status
taxonomy). True SQL verification is Agent 3's SQLGlot job per
INTEGRATION_CONTRACT.md §5; nothing here executes SQL. Every number printed or
appended to eval_results.csv is a real observed model output — no mocking,
no smoothing.
"""
import csv
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from backend import llm_client
from backend.sql_generator import find_unknown_identifiers, generate_sql
from datasets.question_sets import DATASETS
from smoke_test import SCHEMA

EVAL_CSV = "eval_results.csv"
FIELDS = ["run_id", "timestamp", "dataset", "question", "expected", "ground_truth",
          "final_status", "attempts", "empty_before_success", "latency_ms",
          "unknown_ids", "single_select", "sql"]

FIXTURE = {
    "schema": SCHEMA,
    "questions": [  # (expected, question, ground_truth=None) — truths in RESULTS.md
        ("sql", "What is total order revenue by customer region?", None),
        ("sql", "Which product categories sold the most units overall?", None),
        ("sql", "How many orders exist per customer region?", None),
        ("sql", "What is the average order total per customer?", None),
        ("sql", "Which customers have placed more than 5 orders?", None),
        ("clarify", "How have sales been doing recently?", None),
        ("clarify", "Show me the top customers.", None),
        ("clarify", "Are orders increasing?", None),
    ],
}
DATASETS["fixture"] = FIXTURE


def static_check(sql, schema):
    """(unknown_ids, single_select) — schema grounding + read-only shape."""
    unknown = find_unknown_identifiers(sql, schema) if sql else []
    body = (sql or "").strip().rstrip(";")
    single = ";" not in body and body.upper().startswith("SELECT")
    return unknown, single


def run_one(run_id, dataset, expected, question, truth, schema):
    t0 = time.perf_counter()
    out = generate_sql(question, schema)
    empty_retry = False
    attempts = 1
    if out["status"] == "error" and (out.get("error") or {}).get("type") == "empty":
        # mirrors P2a repair-loop policy: one re-prompt for empty completions
        attempts = 2
        empty_retry = True
        out = generate_sql(question, schema)
    ms = int((time.perf_counter() - t0) * 1000)
    sql = out.get("sql")
    unknown, single = static_check(sql, schema) if out["status"] == "ok" else ([], False)
    row = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": dataset,
        "question": question, "expected": expected, "ground_truth": truth or "",
        "final_status": out["status"], "attempts": attempts,
        "empty_before_success": empty_retry and out["status"] == "ok",
        "latency_ms": ms, "unknown_ids": ";".join(unknown),
        "single_select": single, "sql": sql or "",
    }
    return row


def main():
    repeats = 1
    if "--repeats" in sys.argv:
        repeats = int(sys.argv[sys.argv.index("--repeats") + 1])
    dataset = "fixture"
    if "--dataset" in sys.argv:
        dataset = sys.argv[sys.argv.index("--dataset") + 1]
    if dataset not in DATASETS:
        sys.exit(f"unknown dataset {dataset!r} — choose from {sorted(DATASETS)}")
    schema = DATASETS[dataset]["schema"]
    questions = DATASETS[dataset]["questions"]
    run_id = datetime.now(timezone.utc).strftime("%Y%mdT%H%MZ")
    rows = []
    # guard: appending under a stale header silently misaligns every column
    if Path(EVAL_CSV).exists():
        first = open(EVAL_CSV, encoding="utf-8").readline().strip()
        if first and first != ",".join(FIELDS):
            sys.exit(f"{EVAL_CSV} has an outdated header — migrate rows to {FIELDS} first")
    with open(EVAL_CSV, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if fh.tell() == 0:
            writer.writeheader()
        for rep in range(repeats):
            for expected, question, _truth in questions:
                print(f"[{rep + 1}/{repeats}] [{dataset}] {question!r} ...", flush=True)
                row = run_one(run_id, dataset, expected, question, _truth, schema)
                rows.append(row)
                writer.writerow(row)
                fh.flush()
                print(f"    -> {row['final_status']} ({row['latency_ms']} ms)"
                      f" unknown={row['unknown_ids'] or '-'}"
                      f" single_select={row['single_select']}")

    n = len(rows)
    ok = [r for r in rows if r["final_status"] == "ok"]
    clarify = [r for r in rows if r["final_status"] == "clarification_needed"]
    err = [r for r in rows if r["final_status"] == "error"]
    sql_expected = [r for r in rows if r["expected"] == "sql"]
    clarify_expected = [r for r in rows if r["expected"] == "clarify"]
    grounded = [r for r in ok if not r["unknown_ids"] and r["single_select"]]
    lat = [r["latency_ms"] for r in rows]
    empties = [r for r in rows if r["attempts"] == 2]

    print(f"\n===== AGENT 1 LIVE EVAL SUMMARY ({dataset}) =====")
    print(f"runs={n}  model={llm_client.get_config()['model']}  run_id={run_id}")
    print(f"first-attempt empty rate : {len(empties)}/{n} = {len(empties) / n:.0%}")
    print(f"final statuses           : ok={len(ok)} clarify={len(clarify)} error={len(err)}")
    print(f"generation accuracy      : {len([r for r in sql_expected if r['final_status'] == 'ok'])}"
          f"/{len(sql_expected)} non-ambiguous produced SQL")
    print(f"schema-grounded (static) : {len(grounded)}/{len(ok)} of ok outputs use only known identifiers, single SELECT")
    print(f"clarification behaviour  : {len([r for r in clarify_expected if r['final_status'] == 'clarification_needed'])}/{len(clarify_expected)} ambiguous -> clarify; "
          f"false clarifies on clear Qs: {len([r for r in sql_expected if r['final_status'] == 'clarification_needed'])}")
    if lat:
        print(f"latency ms               : min={min(lat)} median={int(statistics.median(lat))} mean={int(statistics.mean(lat))} max={max(lat)}")
    print(f"rows appended to         : {EVAL_CSV}")


if __name__ == "__main__":
    main()
