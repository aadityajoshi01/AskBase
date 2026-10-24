# Agent 1 — Handoff & Integration Guide (Part 10)

Date: 2026-10-24 · Commit series `9bdb0a9..` on `main` · Suite: **60 mocked tests passing, no model/GPU**

## What Agent 1 delivers

| Module | Contract function | Status |
|---|---|---|
| `backend/llm_client.py` | `chat_completion(messages)`, `complete_json(messages)`, `get_config()` | stable, 16 tests |
| `backend/sql_generator.py` | `generate_sql(question, schema, prior_error=None) -> dict` | stable, 21 tests |
| `backend/agent.py` | `synthesize_answer(question, sql, result) -> dict` | stable, 10 tests |
| `backend/repair_loop.py` | `generate_validated_sql(question, schema, validate_sql, generate=None, max_repairs=2)` | stable, 12 tests |

## For Agent 3 (validator) — integration contract
Inject your validator as a callable:

```python
validate_sql(sql: str, dialect: str) -> {"valid": bool, "error": {"type": str, "message": str} | None}
```

`repair_loop` guarantees: every candidate (initial + ≤2 repairs) passes through **your** callable, dialect is taken from `schema["dialect"]` (default `"sqlite"`), and rejected SQL is never returned with `status="ok"`.

## For Agent 4 (orchestrator/UI) — end-to-end usage

```python
from backend.sql_generator import generate_sql          # or:
from backend.repair_loop import generate_validated_sql  # drives generate+validate

gen = generate_validated_sql(question, schema, agent3_validate_sql)
if gen["status"] == "ok":
    result = agent3_execute_then_verify(gen["sql"], db_config)  # you own execution
    final  = synthesize_answer(question, gen["sql"], result)
    # map into the §7 envelope: status/answer/warnings from final,
    # sql/assumptions from gen, rows/columns/row_count/truncated/chart from result
elif gen["status"] == "clarification_needed":
    show(gen["clarification_question"])       # re-ask with the user's answer appended
else:
    show_error(gen["error"])                  # offline/timeout/malformed/repair_exhausted
```

Semantics to preserve in the UI:
- `status="ok", validated=True` → safe to execute. Nothing else may reach execution.
- Empty result is `success` (Agent 1 `synthesize_answer` explains it; don't render as error).
- `invented_identifier` is repaired automatically inside the budget; when exhausted it surfaces as `status="error"` with the last real cause.
- Model offline/timeout ⇒ refusal, never a narrated answer.

## Sample I/O (illustrative structure — not stored results)
Q: “Total order revenue by customer region?” · schema with customers/orders
→ `{"status":"ok","sql":"SELECT c.region, SUM(o.total) AS total_revenue FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.region","assumptions":["total counts gross revenue"]}`
Q: “How have sales been doing recently?” → `{"status":"clarification_needed","clarification_question":"Which time period should ‘recently’ cover?"}`

## Configuration
See `ENDPOINT.md` + root `.env.example`. No endpoint, model name, or key is hardcoded anywhere.

## Known limitations (honest list)
1. Live-model behavior unverified — server not started during this workstream; run `smoke_test.py` after `OfflineAI\start.bat` (Part 9, queued; `RESULTS.md` placeholder pending real numbers).
2. Identifier guard is lexical: qualified refs validate the column part only; a fabricated table name colliding with a real column elsewhere passes — Agent 3's parser is the real boundary.
3. Ambiguity detection quality depends on the 8B model's judgment; mocked tests pin our handling, not model behavior.
4. 4096-token context caps schema size (5000-char truncation); very wide schemas need Agent 2 to prioritize relevant tables.
5. `synthesize_answer` samples the first 50 rows / 3000 chars; answers over larger results are explicitly labeled as sampled.
6. Single dialect assumed (`sqlite`) until Agent 2/3 declare others; the parameter is plumbed but untested against Postgres/MySQL.

## Deliverables checklist vs report §3
✅ module implementation · ✅ prompt/config docs (`PROMPTS.md`, `ENDPOINT.md`) · ✅ sample inputs/outputs (above) · ✅ test results (`TEST_REPORT.md`, 60 tests) · ✅ known limitations · ⏸ live sample run (Part 9)
