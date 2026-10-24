# Agent 2 — Database Intelligence & Data Quality
Branch: `feature/data-profiling`

Owns schema discovery and data-quality profiling for unfamiliar databases, plus a reproducible
synthetic test database. Read-only; never mutates source data.

**Modules:** `backend/schema_discovery.py`, `backend/data_profiler.py`, `sample_data/create_sample_db.py`
**Interfaces:** `get_schema(database_config) -> dict`, `profile_database(database_config) -> dict`

See `../AskBase_AI_Agent_Work_Reports.pdf` §4 for the full spec.

## Current SQLite capability

The first implementation supports arbitrary local SQLite database paths and
uses read-only connections. Both functions accept a path string or a mapping
such as `{"type": "sqlite", "path": "data.sqlite"}`.

```python
from backend.data_profiler import profile_database
from backend.schema_discovery import get_schema

schema = get_schema("sample_data/generated/simple_askbase.sqlite")
profile = profile_database("sample_data/generated/simple_askbase.sqlite")
```

`get_schema()` returns tables/views, row counts, declared SQLite types,
nullability, primary keys, declared foreign keys, observations, warnings, and
a compact `summary_text` compatible with Agent 1's schema context.

`profile_database()` adds per-column null and distinct counts, null rates,
SQLite runtime type counts, exact duplicate-row counts, empty-table warnings,
mixed-type warnings, and foreign-key violations. Warnings are separate from
valid observations and source databases are never modified.

Run Agent 2 tests from this directory:

```bash
python -m pytest tests -q
```
