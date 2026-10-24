# Agent 1 — AI Analyst & SQL Generation
Branch: `feature/ai-agent`

Owns the intelligence layer: natural-language interpretation, schema-aware SQL generation,
bounded repair, and answer synthesis grounded in real query results.

**Boundaries:** Generates SQL and language only. Never executes SQL or bypasses the validator.

**Modules:** `backend/llm_client.py`, `backend/sql_generator.py`, `backend/agent.py`
**Interfaces:** `generate_sql(question, schema) -> dict`, `synthesize_answer(question, sql, result) -> dict`

See `../AskBase_AI_Agent_Work_Reports.pdf` §3 for the full spec.

## Layout (this directory)

```
ai-analyst/
  backend/llm_client.py      # env-configured OpenAI-compatible client, typed errors
  backend/sql_generator.py   # schema-aware generate_sql() + identifier guard
  backend/agent.py           # synthesize_answer() grounded in real results
  backend/repair_loop.py     # bounded repair: every candidate through the same injected validator
  smoke_test.py              # Part 9 live probe — run when start_server.bat is up
  eval_live.py               # live accuracy harness (labeled questions, static checks) -> eval_results.csv
  make_charts.py             # builds report_assets/*.png from recorded CSV + coverage.json
  REPORT.md                  # consolidated work report (tests + coverage + live accuracy + charts)
  AUDIT.md / ENDPOINT.md / PROMPTS.md
  HANDOFF.md / RESULTS.md
  tests/                     # 71 mocked tests (mapping in REPORT.md §5)
  datasets/                  # Kaggle Superstore + C-MAPSS -> SQLite builders, labeled question sets
```

## Running tests (no model or GPU needed)

```
cd ai-analyst
../.venv/Scripts/python.exe -m pytest tests

# with coverage (needs pytest-cov): pip install -r requirements-dev.txt
../.venv/Scripts/python.exe -m pytest tests --cov=backend --cov-report=term
```

## Live evaluation (requires the Qwen3 server running)

```
# 1. start the model server (repo root): start_server.bat
# 2. then, from ai-analyst/:
cd ai-analyst
pip install -r requirements-dev.txt          # once — matplotlib + pytest-cov
../.venv/Scripts/python.exe eval_live.py --repeats 2
../.venv/Scripts/python.exe make_charts.py
```

`eval_live.py` pushes labeled questions through the real `generate_sql()` and
records status, latency and static-groundedness checks to `eval_results.csv`.
These are static checks (identifier guard + single-SELECT shape) recorded before Agent 3's SQLGlot
validator merged, so they carry no execution evidence. `make_charts.py` renders
`report_assets/` only from those recorded files; no numbers are hand-entered.
Current consolidated findings: [`REPORT.md`](REPORT.md) — latest full live run
(`202610dT1324Z`/`1326Z`) covers all three datasets, 66 rows, 2 repeats each.

## Kaggle test datasets (`datasets/`)

Real evaluation data beyond the mini fixture — Superstore (Kaggle) and C-MAPSS
turbofan degradation (NASA), rebuilt into SQLite by one stdlib script:

```
cd ai-analyst
../.venv/Scripts/python.exe datasets/build_datasets.py   # -> datasets/*.db (gitignored)
../.venv/Scripts/python.exe eval_live.py --dataset superstore   # or: cmaps
../.venv/Scripts/python.exe make_charts.py superstore           # charts per dataset
```

[`datasets/question_sets.py`](datasets/question_sets.py) holds each dataset's
schema summary plus labeled questions (`sql` with **sqlite-computed ground
truths**, `clarify` with the ambiguity documented) — the truths let Agent 4
compare generated SQL by *result*, not just static shape. Gotchas recorded so
they are never re-introduced: the Kaggle Superstore `train.csv` has **no
Quantity/Discount columns** and its dates are **DD/MM/YYYY** (verified: 0
unparseable, ship−order lag 0–7 days; MM/DD implies impossible 4-month lags).

## Results at a glance (live model, recorded data only)

66 labeled questions across three datasets (mini fixture + Kaggle Superstore + NASA
C-MAPSS), real Qwen3-8B, 2 repeats each. Accuracy metrics are static checks —
identifier guard & single-SELECT shape, recorded before SQLGlot validation went live:

| | fixture | superstore | cmaps | total |
|---|---|---|---|---|
| SQL produced for clear questions | 8/10 | 18/20 | 16/16 | **42/46 (91%)** |
| Accepted SQL schema-grounded | 8/8 | 18/18 | 18/18 | **44/44 (100%)** |
| Ambiguous → clarification | 6/6 | 8/8 | 4/6 | **18/20 (90%)** |
| Median latency | 3.2 s | 2.5 s | 2.8 s | **~2.8 s** |

Empty completions (the earlier failure mode) were root-caused to Qwen3 thinking tokens
and eliminated via `/no_think` in the system prompts — 0/66 rows. Charts per dataset:

![Fixture accuracy](report_assets/accuracy_charts.png)
![Superstore accuracy](report_assets/accuracy_charts_superstore.png)
![C-MAPSS accuracy](report_assets/accuracy_charts_cmaps.png)

Mocked test suite and per-module coverage:

![Tests and coverage](report_assets/tests_and_coverage.png)

Full analysis of every failure (2 invented-column rejections, 2 Postgres-syntax rejections,
1 silent-threshold clarification miss) in [`REPORT.md`](REPORT.md).

Integration note: a fresh root-level `backend/` merge is Agent 4's call at Gate 6 (report §8).
