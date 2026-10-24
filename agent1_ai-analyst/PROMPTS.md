# Agent 1 — Prompt & Configuration Reference

## 1. `sql_generator.SYSTEM_PROMPT` (SQL generation)
Purpose: map one NL question to ONE read-only SQL for the given schema.
Key rules encoded (see module for exact text):
- ONLY schema-provided names; no invented currencies/units/periods.
- Material ambiguity → `clarification_needed` + one specific question, four enumerated
  criteria: missing time period, unmapped metric, entity/join double meaning,
  aggregation basis.
- Immaterial choices (sort, tie-break, limit, naming) → default + record in `assumptions`.
- Output: strict JSON object only.

Tuning notes for the 8B Qwen3 model:
- Keep JSON-only instruction last — small models follow final-format instructions better.
- If the model wraps output, `complete_json()` already tolerates fences/prose.
- Temperature default 0.1; raise toward 0.3 only if outputs get rigid on paraphrase tests.

## 2. `agent.SYSTEM_PROMPT` (answer synthesis)
- 1–3 short sentences, plain text, ONLY provided columns/rows.
- Explicitly forbids adding numbers/units/comparisons not in the data.
- Empty-results path never calls the model (deterministic message in code).

## 3. `repair_loop` prompt injection
On repair, `generate_sql(question, schema, prior_error)` appends:
“Your previous attempt failed validation/execution with: {type}: {message}. Fix the query and try again.”
Only validator/identifier errors are injected — transport errors never re-prompt the model.

## 4. Environment configuration
All in `.env.example`; module defaults mirror it. Priority: env > defaults.
`LLM_BASE_URL LLM_MODEL LLM_API_KEY LLM_TIMEOUT_S LLM_MAX_TOKENS LLM_TEMPERATURE`

## 5. Token budget arithmetic
Server context 4096. Prompt ≈ system (~600 tok) + schema block (≤5000 chars ≈ ~1300 tok)
+ question/rows + `LLM_MAX_TOKENS` (1024) — headroom checked for the fixture schema;
Agent 2 must keep schema summaries compact for large databases (see HANDOFF limitation 4).
