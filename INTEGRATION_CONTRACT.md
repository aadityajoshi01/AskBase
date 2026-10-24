# AskBase — Integration Contract (Gate 2)

Status: **PROPOSED by Agent 1, pending sign-off from Agents 2–4.**
Sign-off = edit this file adding your name/date below, one commit per agent (report §8 Gate 2).
Derived from `AskBase_AI_Agent_Work_Reports.pdf` §7 and `agent1_ai-analyst/CONTRACT_PROPOSAL.md`.

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
  "verification": {"passed": true},
  "chart": {"type": "bar", "x": "…", "y": "…"} ,
  "assumptions": [], "clarification_question": null,
  "error": {"type": "offline|timeout|http|malformed|empty|invalid_sql|execution|repair_exhausted", "message": "…"}
}
```

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
2. Repairs: max 2 after initial generation, shared budget across all retryable error types.
3. Retryable error types (repair loop re-prompts): `invented_identifier`, validator rejections, and — **per live evidence, RESULTS.md finding 1** — `empty` completions. Transport types (`offline`, `timeout`, `http`) fail fast, no re-prompt.
4. Agent 1 never executes SQL; Agent 2 never mutates source data; Agent 3 rejects writes/DDL/multi-statement/transaction control; Agent 4 never bypasses layers.
5. Failed execution or failed verification can never render as a successful insight.

## 6. Clarification round-trip (§7 open item 3 — Agent 4 decision)
Proposal: UI shows `clarification_question`; on user reply, orchestrator re-calls
`generate_sql` with `question + " " + user_answer` appended. No server-side session state.

## 7. Sign-off
| Agent | Name/date | Notes |
|---|---|---|
| Agent 1 | Aaditya / 2026-10-24 | proposes all of the above |
| Agent 2 | ☐ | confirm §4 schema shape |
| Agent 3 | ☐ | confirm §5 validator injection signature `{valid, error}` |
| Agent 4 | ☐ | confirm §3 envelope + §6 round-trip; owns future edits |
