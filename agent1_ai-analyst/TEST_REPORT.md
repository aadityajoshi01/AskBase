# Agent 1 Test Report (Part 8)

Suite: **60 mocked tests, 0 external services, no model or GPU required** (report §3 requirement).
Run: `cd agent1_ai-analyst && ../.venv/Scripts/python.exe -m pytest tests` — 60 passed in 0.35s (2026-10-24).

Requirement checklist from report §3 — “at least 15 mocked-LLM tests covering …”:

| Required area | Covering tests | File |
|---|---|---|
| Filters | `test_string_literals_do_not_count_as_identifiers` (WHERE region='West') | test_sql_generator |
| Joins | `test_join_with_aliases_passes_guard`, `test_valid_generation_returns_ok` | test_sql_generator |
| Aggregation | SUM/GROUP BY/ORDER BY/LIMIT in join + defaults tests | test_sql_generator |
| Ambiguity | `test_missing_time_period_returns_clarification`, `test_metric_not_in_schema_returns_clarification`, `test_clarification_never_leaks_sql`, `test_clarification_passthrough`, `test_clarification_without_question_is_error` | test_sql_generator |
| Invalid schema | `test_empty_schema_flags_all_identifiers_as_invented`, `test_schema_missing_optional_keys_is_tolerated` | test_coverage_gaps |
| Malformed output | `test_unknown_status_is_error`, `test_ok_status_without_sql_is_error`, client: non-JSON / missing choices / invalid / non-object JSON | test_sql_generator, test_llm_client |
| Offline server | `test_llm_offline_returns_error_dict`, client `test_connection_error_maps_to_offline`, synthesis `test_model_offline_returns_error_without_fabrication` | all three |
| Timeout | `test_llm_timeout_returns_error_dict`, client `test_timeout_maps_to_timeout` | generator, client |
| Repair limits | `test_repair_capped_at_two_then_error` (never a 4th), `test_invented_identifier_repair_stops_at_cap` | test_repair_loop, test_coverage_gaps |
| Empty results | `test_empty_result_is_success_not_error` (deterministic, model untouched) | test_agent |
| Insufficient evidence | `test_verification_mismatch_refused_even_with_rows`, `test_failed_verification_is_refused`, `test_failed_execution_is_refused` | test_agent |

Additional guarantees with dedicated tests: single-execution-path (every candidate through the same
injected validator), repair feedback carries the real validator error, clarification short-circuits,
schema context bounded for 4096-token model, no DB access anywhere in Agent 1 modules (source-scan guards).

## Semantic note (decided in Part 8, needs Gate 2 ratification)
`invented_identifier` generation errors are treated as **repairable** (re-counted inside the ≤2 repair
budget); transport/malformed errors are not. Rationale: report §3 lists repairable SQL errors after
initial generation; an invented column is exactly that class.
