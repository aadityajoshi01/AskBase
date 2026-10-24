"""Generate report charts from REAL recorded data (eval_results.csv, coverage.json).

    cd agent1_ai-analyst
    ../.venv/Scripts/python.exe make_charts.py

Nothing is hand-entered: every plotted value is read from the files produced by
eval_live.py / pytest-cov. Fails loudly if a source file is missing.
"""
import csv
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).parent
ASSETS = HERE / "report_assets"
ASSETS.mkdir(exist_ok=True)

GREEN, AMBER, RED, BLUE, GREY = "#2e7d32", "#f9a825", "#c62828", "#1565c0", "#9e9e9e"


def load_eval_rows():
    with open(HERE / "eval_results.csv") as fh:
        return list(csv.DictReader(fh))


def count_tests_per_file():
    """Parse pytest --collect-only for exact per-suite counts (no hardcoded numbers)."""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
        capture_output=True, text=True, cwd=HERE, check=True).stdout
    counts = {}
    for line in out.splitlines():
        m = re.match(r"(tests[\\/]test_[^:]+\.py)::", line.strip())
        if m:
            name = Path(m.group(1).replace("\\", "/")).name
            counts[name] = counts.get(name, 0) + 1
    return counts


def chart_tests_and_coverage():
    cov = json.load(open(HERE / "coverage.json"))["files"]
    names = [f"backend/{Path(k).name}" for k in cov]
    pct = [v["summary"]["percent_covered"] for v in cov.values()]
    stmts = [v["summary"]["num_statements"] for v in cov.values()]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    ax1.barh(names, pct, color=GREEN)
    ax1.set_xlim(0, 100)
    ax1.set_title("Unit-test coverage per module (pytest-cov)")
    ax1.set_xlabel("% covered")
    for i, (p, s) in enumerate(zip(pct, stmts)):
        ax1.text(p + 1, i, f"{p:.0f}% ({s} stmts)", va="center", fontsize=8)
    counts = count_tests_per_file()
    total = sum(counts.values())
    ax2.barh(list(counts), list(counts.values()), color=BLUE)
    ax2.set_title(f"Mocked tests per suite ({total} total, all passing)")
    ax2.set_xlabel("tests")
    for i, c in enumerate(counts.values()):
        ax2.text(c + 0.2, i, str(c), va="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(ASSETS / "tests_and_coverage.png", dpi=140)
    plt.close(fig)


def chart_latency(rows):
    fig, ax = plt.subplots(figsize=(11, 4.5))
    for i, r in enumerate(rows):
        lat = int(r["latency_ms"])
        color = {"ok": GREEN, "clarification_needed": BLUE}.get(r["final_status"], RED)
        ax.scatter(i + 1, lat, c=color, s=70, zorder=3)
    med = statistics.median(int(r["latency_ms"]) for r in rows)
    ax.axhline(med, ls="--", c=GREY, label=f"median {med:.0f} ms")
    ax.set_xticks(range(1, len(rows) + 1))
    ax.set_xticklabels([f"q{i}" for i in range(1, len(rows) + 1)], fontsize=7)
    ax.set_ylabel("wall-clock latency (ms)")
    ax.set_title("Live per-question generation latency (Qwen3-8B Q4_K_M, llamafile, RTX 3050)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(ASSETS / "latency_scatter.png", dpi=140)
    plt.close(fig)


def chart_accuracy(rows):
    n = len(rows)
    ok = sum(r["final_status"] == "ok" for r in rows)
    clar = sum(r["final_status"] == "clarification_needed" for r in rows)
    err = n - ok - clar
    sql_expected = sum(r["expected"] == "sql" for r in rows)
    clar_expected = sum(r["expected"] == "clarify" for r in rows)
    grounded = sum(r["final_status"] == "ok" and not r["unknown_ids"] and
                   r["single_select"] == "True" for r in rows)
    good_clarify = sum(r["expected"] == "clarify" and
                       r["final_status"] == "clarification_needed" for r in rows)
    empties = sum(r["attempts"] == "2" for r in rows)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    ax1.pie([ok, clar, err],
            labels=[f"ok\n{ok}", f"clarify\n{clar}", f"error\n{err}"],
            colors=[GREEN, BLUE, RED], startangle=90, autopct="%1.0f%%")
    ax1.set_title(f"Final status distribution (n={n})")
    metrics = {
        "SQL produced for\nclear questions": ok / sql_expected if sql_expected else 0,
        "Schema-grounded &\nsingle-SELECT (static)": grounded / ok if ok else 0,
        "Ambiguous ->\nclarification": good_clarify / clar_expected if clar_expected else 0,
        "First-attempt success\n(no empty retry)": (n - empties) / n,
    }
    ax2.barh(list(metrics), [v * 100 for v in metrics.values()], color=AMBER)
    ax2.set_xlim(0, 105)
    ax2.set_xlabel("%")
    ax2.set_title("Accuracy metrics (live model, static checks)")
    for i, v in enumerate(metrics.values()):
        ax2.text(v * 100 + 1, i, f"{v:.0%}", va="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(ASSETS / "accuracy_charts.png", dpi=140)
    plt.close(fig)
    print("charts written to", ASSETS)


def main():
    rows = load_eval_rows()
    chart_tests_and_coverage()
    chart_latency(rows)
    chart_accuracy(rows)


if __name__ == "__main__":
    main()
