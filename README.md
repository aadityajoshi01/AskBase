# AskBase — From Questions to Verified SQL Insights

AI-powered data analyst: connect a database, ask a question in plain English, get an answer
grounded in verified SQL execution, with result tables, charts and transparent data-quality warnings.

## Workstreams (four parallel engineering agents)

| Directory | Agent | Owns |
|-----------|-------|------|
| `agent1_ai-analyst/` | Agent 1 — AI Analyst & SQL Generation | NL interpretation, schema-aware SQL gen, bounded repair, answer synthesis |
| `agent2_data-intelligence/` | Agent 2 — Database Intelligence & Data Quality | Schema discovery, profiling, synthetic test DB (read-only) |
| `agent3_sql-safety/` | Agent 3 — SQL Safety, Execution & Verification | Validation, restricted execution, result verification |
| `agent4_ui-evaluation/` | Agent 4 — UI, Evaluation & Integration | App UI, orchestration, evaluation, integration |

## Quickstart (all agents)

```bash
# one-time setup (Python 3.12+; Windows: use `py -3.12` if `python` is missing)
# ⚠ the repo URL currently ends with a literal "." — keep it or GitHub 404s.
# On Windows, clone into an explicit dir name (folders can't end in "."):
git clone <this-repo-url-with-trailing-dot> repo && cd repo
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Windows
# .venv/bin/pip install -r requirements.txt          # macOS/Linux

# run the tests — per agent directory (CI does the same; do not run one
# repo-wide pytest: agents share top-level package names like `backend/`)
.venv/Scripts/python.exe -m pytest -q agent1_ai-analyst/tests   # or cd into any agentN dir and run: python -m pytest tests
```

The LLM server (Agent 1 work) is Qwen3-8B via llamafile — start it with `start_server.bat`,
stop with `stop_server.bat`. No live model is needed for tests; they are fully mocked.

Before coding against another agent's module, check [`INTEGRATION_CONTRACT.md`](INTEGRATION_CONTRACT.md)
(Gate 2) — it fixes the interfaces, statuses and response envelope everyone must conform to.

## Reference

Full specification: [`AskBase_AI_Agent_Work_Reports.pdf`](AskBase_AI_Agent_Work_Reports.pdf)

- §2 Target architecture and module layout
- §3–§6 Per-agent responsibilities, interfaces, testing, acceptance
- §7 Shared integration contract and response envelope
- §8 Integration sequence and test gates (Gate 1–9)
- §9 Final acceptance checklist · §10 Demonstration plan

> This structure is a target architecture. The actual source tree, DB types, UI framework and current
> implementation must be confirmed by inspecting the code before coding against specific paths.
