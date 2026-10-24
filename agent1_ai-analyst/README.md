# Agent 1 — AI Analyst & SQL Generation
Branch: `feature/ai-agent`

Owns the intelligence layer: natural-language interpretation, schema-aware SQL generation,
bounded repair, and answer synthesis grounded in real query results.

**Boundaries:** Generates SQL and language only. Never executes SQL or bypasses the validator.

**Modules:** `backend/llm_client.py`, `backend/sql_generator.py`, `backend/agent.py`
**Interfaces:** `generate_sql(question, schema) -> dict`, `synthesize_answer(question, sql, result) -> dict`

See `../AskBase_AI_Agent_Work_Reports.pdf` §3 for the full spec.
