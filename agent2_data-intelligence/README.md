# Agent 2 — Database Intelligence & Data Quality
Branch: `feature/data-profiling`

Owns schema discovery and data-quality profiling for unfamiliar databases, plus a reproducible
synthetic test database. Read-only; never mutates source data.

**Modules:** `backend/schema_discovery.py`, `backend/data_profiler.py`, `sample_data/create_sample_db.py`
**Interfaces:** `get_schema(database_config) -> dict`, `profile_database(database_config) -> dict`

See `../AskBase_AI_Agent_Work_Reports.pdf` §4 for the full spec.
