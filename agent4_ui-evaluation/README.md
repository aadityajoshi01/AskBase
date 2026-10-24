# Agent 4 — UI, Evaluation & Integration
Branch: `feature/ui-evaluation`

Owns the usable application and end-to-end orchestration: connection/question UI, answer + SQL +
table + chart + warnings rendering, evaluation set, and integration of Agents 1–3.

**Modules:** `app.py` and UI layer; owns `INTEGRATION_CONTRACT.md`
**Orchestration:** `run_analysis(question, database_config) -> dict`

See `../AskBase_AI_Agent_Work_Reports.pdf` §6 for the full spec.
