# AskBase — Integration Contract (Gate 2)

Status: **PROPOSED by Agent 1, pending sign-off from Agents 2–4.**
Sign-off = edit this file adding your name/date below, one commit per agent (report §8 Gate 2).
Implemented 2026-10-24 by the repository-root `gateway/` package (Agent 2 schema →
Agent 1 repair loop → Agent 3 validate/execute/verify → Agent 1 synthesis → §3 envelope);
the Agent 4 UI calls it at http://127.0.0.1:8010.
Derived from `AskBase_AI_Agent_Work_Reports.pdf` §7 (the earlier
`ai-analyst/CONTRACT_PROPOSAL.md` was merged into this file and retired).

## 1. Module interfaces (owners in parentheses)

```
get_schema(database_config) -> dict                  (Agent 2)
profile_database(database_config) -> dict            (Agent 2)
generate_sql(question, schema, prior_error=None) -> dict        (Agent 1)
validate_sql(sql, dialect) -> dict                   (Agent 3)
execute_query(sql, database_config) -> dict          (Agent 3)
verify_result(question, result) -> dict              (Agent 3)
synthesize_answer(question, sql, result) -> dict     (Agent 1)
generate_validated_sql(question, schema, validate_sql, ...) -> dict   (Agent 1 helper; Agent 4 may drive)
run_analysis(question, database_config) -> dict      (Agent 4 orchestrator)
```

## 2. Status vocabulary
`success` · `clarification_needed` · `error`.
Empty result set = `success` with `row_count: 0` and an explanatory answer — never `error`.
Truncated/partial results carry `truncated: true` and a matching warning string.

## 3. Response envelope (Agent 4 assembles)
```json
{
  "status": "success | clarification_needed | error",
  "answer": "…grounded in rows…",
  "sql": "SELECT …",
  "columns": [], "rows": [], "row_count": 0, "truncated": false,
  "warnings": [],
  "verification": {"passed": true, "message": "…", "checks": ["…"]},
  "chart": {"type": "bar | line", "x": "…", "y": "…"} ,
  "assumptions": [], "clarification_question": null, "attempts": 1,
  "error": {"type": "offline|timeout|http|malformed|empty|invalid_sql|invented_identifier|repair_exhausted|execution",
            "detail": "the agent's own unmodified reason, e.g. database_unavailable", "message": "…"},
  "meta": {"durationMs": 0, "readOnly": true, "code": "success|…", "database": "…"}
}
```
`verification.message`/`.checks` are the gateway's rendering of Agent 3's verdict
(`explanation` / `checks_passed`); the raw `failures` and `evidence` ride along for the UI.
`error.type` is the contracted vocabulary above; `error.detail` preserves the exact producer
type so the UI can show a specific reason without every agent re-learning the enum.

`chart` is only sent for a two-column, numeric-y result of 2–12 rows. `type` is `"line"` when
every x label is an ISO date/year/month period **and** the rows arrive ascending; anything else
(unordered periods, categories, non-date labels) stays `"bar"`. The UI renders only what the rows
say — it never re-sorts, interpolates or adds values to draw a chart.

`database_config` for Agents 2, 3 and the gateway: `{"path": "<sqlite file>", "dialect?": "sqlite",
"max_rows?": 1..100000, "timeout_s?": 0.05..300}`. Unknown keys are ignored; `path` is required.

## 4. Schema dict shape (Agent 2 to confirm — §7 open item 1)
```json
{"dialect": "sqlite",
 "tables": [{"name": "…", "columns": [{"name": "…", "type": "…",
   "pk": true, "fk_to": "other_table.col"}]}],
 "warnings": ["…"]}
```
Agent 1 accepts missing `type`/`pk`/`fk_to`/`warnings` keys and truncates the rendered
context at 5000 chars — Agent 2 should prioritize tables relevant to the question
when the schema is very wide.

## 5. Safety invariants (all agents)
1. Every SQL candidate — initial or repaired — passes Agent 3's `validate_sql` before execution. No other path to execution exists.
2. Repairs: max 2 after initial generation — **three SQL attempts per analysis in total**, and the
   budget is shared. The gateway (`gateway/orchestrator.py`) counts attempts across validator
   rejections *and* engine rejections, so a query that only fails once SQLite sees it still gets
   its remaining attempts rather than a free extra try.
3. Retryable error types (repair loop re-prompts): `invented_identifier`, validator rejections,
   engine `invalid_sql` (unknown table/column/function — the model's own mistake, per live evidence
   on the Superstore set), and — **per live evidence, RESULTS.md finding 1** — `empty` completions.
   Transport types (`offline`, `timeout`, `http`) and infrastructure types
   (`database_unavailable`, `invalid_config`, `permission_denied`) fail fast, no re-prompt.
4. Agent 1 never executes SQL; Agent 2 never mutates source data; Agent 3 rejects writes/DDL/multi-statement/transaction control; Agent 4 never bypasses layers.
5. Failed execution or failed verification can never render as a successful insight.

## 6. Clarification round-trip (§7 open item 3 — Agent 4 decision)
Proposal: UI shows `clarification_question`; on user reply, orchestrator re-calls
`generate_sql` with `question + " " + user_answer` appended. No server-side session state.

## 7. Sign-off
| Agent | Name/date | Notes |
|---|---|---|
| Agent 1 | Aaditya / 2026-10-24 | proposes all of the above; shipped `gateway/` implementing §1–§5 |
| Agent 2 | ☐ | confirm §4 schema shape — `get_schema()` output already matches (§4) |
| Agent 3 | ☐ | §1 signatures confirmed against your merged implementation (PR #3): `validate_sql` returns `{status, valid, normalized_sql, tables, statement_kind, error, checks_passed}`, `execute_query(sql, database_config)` re-validates inline, `verify_result` verdict keys as shipped. Gateway consumes all three; §5.2/3 now record the shared attempt budget |
| Agent 4 | ☐ | confirm §3 envelope + §6 round-trip; UI now calls `POST /api/analyze` live (mock kept via `features.liveBackend=false` or `ASKBASE_MOCK_FALLBACK=true`). **Needs your review:** the UI now also calls `POST /api/datasets/upload` (see §7.1) to ingest a CSV, so `canAnalyzeDataset` was tightened — a locally parsed CSV is not analyzable even with a live gateway until it is ingested and addressed by registered id. Upload cap aligned to the gateway's 5 MB |

## 7.1 CSV ingestion (gateway-side, added 2026-10-24)

`POST /api/datasets/upload` with `{"fileName": "orders.csv", "csv": "<text>"}` rebuilds the file
server-side into SQLite under `gateway/uploads/` (gitignored), registers it, and answers
`201 {"status":"ok","database":{id,label,table,row_count,columns,source},"schema":<§4>,"profile":…}`.
Rejections are `400 {"status":"error","message":…}` — never a partially registered dataset.
Limits: 5 MB, 60 columns, 200k rows, 4000 chars per cell; identifiers are slugified and
deduplicated, values are parameter-bound, blank cells become NULL (not `''`, which SQLite
casts to 0 inside `SUM`). The returned id then works as `databaseConfig.database` on
`/api/analyze` with no other contract change.
