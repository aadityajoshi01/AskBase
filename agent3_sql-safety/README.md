# Agent 3 — SQL Safety, Execution & Verification
Branch: `feature/sql-verification`

Owns the safety boundary between model SQL and the database: dialect-aware validation,
read-only execution with timeouts/row limits, and result verification against actual rows.

**Modules:** `backend/sql_validator.py`, `backend/query_executor.py`, `backend/result_verifier.py`
**Interfaces:** `validate_sql(sql, dialect) -> dict`, `execute_query(sql, database_config) -> dict`, `verify_result(question, result) -> dict`

See `../AskBase_AI_Agent_Work_Reports.pdf` §5 for the full spec.
