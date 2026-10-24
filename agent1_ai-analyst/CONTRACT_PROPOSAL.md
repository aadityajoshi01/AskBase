# Agent 1 — Integration Contract Proposal (Part 1.3)

Proposed by Agent 1, to be reconciled in Gate 2 with Agents 2–4. Aligned with report §7.

## Shared status vocabulary
`success` · `clarification_needed` · `error` — plus `empty` as a **result property**, never an error.

## Agent 1 interfaces (generation & synthesis only — never executes SQL)

```python
generate_sql(question: str, schema: dict) -> dict
# schema: output of Agent 2 get_schema(), optionally with Agent 2 profile warnings attached
# returns:
{
  "status": "ok" | "clarification_needed" | "error",
  "sql": "SELECT ..." | None,
  "assumptions": ["..."],                  # interpretations the model made
  "clarification_question": str | None,    # required when status=clarification_needed
  "error": {"type": "offline|timeout|malformed|...", "message": str} | None
}

synthesize_answer(question: str, sql: str, result: dict) -> dict
# result: verified output of Agent 3 execute_query/verify_result
# returns:
{
  "status": "success" | "error",
  "answer": str,          # grounded strictly in result rows/columns
  "warnings": ["..."],    # limitations: truncation, sampling, nulls affecting totals
  "evidence": {"row_count": int, "columns": [...]}
}
```

## Repair loop (with Agent 3)
- Validator/executor errors re-enter generation **max 2 times**; repaired SQL passes the **same** `validate_sql` path (report §3/§5 single-execution-path rule).

## Failure modes Agent 1 must never produce
- Executed or self-run SQL; success answers on failed execution; invented columns,
  currencies, units or time periods; silently chosen interpretations of material ambiguity.

## Envelope fit
`run_analysis` (owned by Agent 4) maps Agent 1 outputs into the §7 response envelope:
`status/answer/sql/warnings` come from these interfaces; `rows/columns/row_count/truncated/chart/verification` come from Agent 3.

## Open questions for Gate 2
1. Exact shape of Agent 2's `schema` dict (table/column/type/PK/FK keys).
2. Does the repair loop live in Agent 1 or in Agent 4's orchestrator? (Proposal: Agent 4 drives it; Agent 1 exposes the two interfaces above only.)
   **RESOLVED 2026-10-24 (Part 7):** Agent 1 ships `backend/repair_loop.py::generate_validated_sql(question, schema, validate_sql, ...)` implementing the contracted semantics (1 initial + ≤2 repairs, same injected validator for every candidate). Agent 4 may call it or drive the loop itself — behavior is identical either way.
3. Clarification round-trip: how UI re-submits a question with the user's clarification appended.
