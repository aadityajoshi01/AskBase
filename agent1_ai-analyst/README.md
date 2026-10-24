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
agent1_ai-analyst/
  backend/llm_client.py      # env-configured OpenAI-compatible client, typed errors
  backend/sql_generator.py   # schema-aware generate_sql() + identifier guard
  backend/agent.py           # synthesize_answer() grounded in real results
  backend/repair_loop.py     # bounded repair: every candidate through the same injected validator
  AUDIT.md / ENDPOINT.md / CONTRACT_PROPOSAL.md
  tests/                     # 55 mocked tests
```

## Running tests (no model or GPU needed)

```
cd agent1_ai-analyst
../.venv/Scripts/python.exe -m pytest tests
```

Integration note: a fresh root-level `backend/` merge is Agent 4's call at Gate 6 (report §8).
