# Agent 3 — SQL Safety, Execution & Verification
Branch: `feature/sql-verification`

Owns the safety boundary between model-generated SQL and the database: dialect-aware validation,
read-only execution with timeouts and row limits, and structural verification of the result
envelope an execution returned.

**Modules:** `backend/sql_validator.py`, `backend/query_executor.py`, `backend/result_verifier.py`
**Interfaces:** `validate_sql(sql, dialect) -> dict`, `execute_query(sql, database_config) -> dict`, `verify_result(question, result) -> dict`

## Status

| Interface | Status |
|---|---|
| `validate_sql` | implemented — fail closed, never raises |
| `execute_query` | implemented for **SQLite only**, read-only, never raises |
| `verify_result` | implemented — envelope consistency only, **not wired into the other two** |

Execution opens the configured file with `mode=ro`, runs only the validator's `normalized_sql`,
caps rows at `max_rows` and reports `truncated` with a matching warning, enforces a cooperative
`timeout_s` deadline through SQLite's progress handler, and maps every failure to a controlled
`error.type` whose message never contains raw exception text, filesystem paths or credentials.
An empty result set is `status="success"` with `row_count: 0`, never an error.

`verify_result(question, result)` answers a narrow question: **is this result envelope internally
consistent, and did execution report success?** It checks that every required field is present and
well-typed, that `row_count` matches the rows returned and each row has one value per column, that
an empty result is treated as a success rather than a failure, that a truncated result discloses
itself with a matching warning, and that a failed execution can never be marked verified.
`passed=True` therefore establishes **structure, not factual correctness**: it does not show the
query answered the user's question, and it performs no count/total/ranking check against the rows —
those figures live in Agent 1's `answer` text, which this interface never receives. The verdict
carries `scope: "envelope_consistency"` and a passing explanation that says the same thing, so a
caller cannot accidentally present this as evidence that the data is right.
It is deliberately isolated for now: `execute_query` does not call it, Agent 1 does not call it, and
whether a *missing* `verification` should count as passed is still an open contract question
(`INTEGRATION_CONTRACT.md` §3; Agent 1's `backend/agent.py:58-60` currently accepts a missing one).

`database_config`'s shape (`{"dialect", "path", "timeout_s", "max_rows"}`) is **Agent 3's proposal,
not an agreed interface** — `INTEGRATION_CONTRACT.md` still defines no keys for the parameter that
Agents 2, 3 and 4 share. Expect it to change when they sign off.

Parsing Postgres or MySQL is not the same as running them: those dialects validate, and
`execute_query` rejects them with `unsupported_dialect` before opening any connection.

## Tests

```
cd agent3_sql-safety
../.venv/Scripts/python -m pytest tests -q          # 277 passing; no live model or GPU needed
```

Run from inside this directory — each agent owns top-level package names like `backend/`, so a
single repo-wide pytest run would let one agent's package shadow another's. Execution tests create
throwaway SQLite files under `tmp_path` and assert the file bytes are unchanged afterwards.
Verifier tests are self-contained: they build envelopes directly and also assert against real
`execute_query` output, without the verifier being wired into it.

## Further detail

`SUPPORTED_SQL.md` — accepted and rejected SQL, the denied-function denylist, the result and
verification envelopes, the error-category table, what `verify_result` does and does not establish,
and the timeout and security limitations stated in full.
See `../AskBase_AI_Agent_Work_Reports.pdf` §5 for the original spec.
