"""Agent 1 live evaluation — accuracy + latency against the real Qwen3 server.

    cd agent1_ai-analyst
    ../.venv/Scripts/python.exe eval_live.py [--repeats N]

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

from backend import llm_client
from backend.sql_generator import find_unknown_identifiers, generate_sql
from smoke_test import SCHEMA

EVAL_CSV = "eval_results.csv"
FIELDS = ["run_id", "timestamp", "question", "expected", "final_status",
          "attempts", "empty_before_success", "latency_ms", "unknown_ids",
          "single_select", "sql"]

# expected: "sql" = material question with a definite answer in this schema;
#           "clarify" = materially ambiguous (missing period / unmapped metric).
QUESTIONS = [
    ("sql", "What is total order revenue by customer region?"),
    ("sql", "Which product categories sold the most units overall?"),
    ("sql", "How many orders exist per customer region?"),
    ("sql", "What is the average order total per customer?"),
    ("sql", "Which customers have placed more than 5 orders?"),
    ("clarify", "How have sales been doing recently?"),
    ("clarify", "Show me the top customers."),
    ("clarify", "Are orders increasing?"),
]


def static_check(sql):
    """(unknown_ids, single_select) — schema grounding + read-only shape."""
    unknown = find_unknown_identifiers(sql, SCHEMA) if sql else []
    body = (sql or "").strip().rstrip(";")
    single = ";" not in body and body.upper().startswith("SELECT")
    return unknown, single


def run_one(run_id, expected, question):
    t0 = time.perf_counter()
    out = generate_sql(question, SCHEMA)
    empty_retry = False
    attempts = 1
    if out["status"] == "error" and (out.get("error") or {}).get("type") == "empty":
        # mirrors P2a repair-loop policy: one re-prompt for empty completions
        attempts = 2
        empty_retry = True
        out = generate_sql(question, SCHEMA)
    ms = int((time.perf_counter() - t0) * 1000)
    sql = out.get("sql")
    unknown, single = static_check(sql) if out["status"] == "ok" else ([], False)
    row = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question": question, "expected": expected,
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
    run_id = datetime.now(timezone.utc).strftime("%Y%mdT%H%MZ")
    rows = []
    with open(EVAL_CSV, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if fh.tell() == 0:
            writer.writeheader()
        for rep in range(repeats):
            for expected, question in QUESTIONS:
                print(f"[{rep + 1}/{repeats}] {question!r} ...", flush=True)
                row = run_one(run_id, expected, question)
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

    print("\n===== AGENT 1 LIVE EVAL SUMMARY =====")
    print(f"runs={n}  model={llm_client.get_config()['model']}  run_id={run_id}")
    print(f"first-attempt empty rate : {len(empties)}/{n} = {len(empties) / n:.0%}")
    print(f"final statuses           : ok={len(ok)} clarify={len(clarify)} error={len(err)}")
    print(f"generation accuracy      : {len(ok)}/{len(sql_expected)} non-ambiguous produced SQL")
    print(f"schema-grounded (static) : {len(grounded)}/{len(ok)} of ok outputs use only known identifiers, single SELECT")
    print(f"clarification behaviour  : {len([r for r in clarify_expected if r['final_status'] == 'clarification_needed'])}/{len(clarify_expected)} ambiguous -> clarify; "
          f"false clarifies on clear Qs: {len([r for r in sql_expected if r['final_status'] == 'clarification_needed'])}")
    if lat:
        print(f"latency ms               : min={min(lat)} median={int(statistics.median(lat))} mean={int(statistics.mean(lat))} max={max(lat)}")
    print(f"rows appended to         : {EVAL_CSV}")


if __name__ == "__main__":
    main()
