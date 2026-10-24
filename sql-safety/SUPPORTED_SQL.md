# Agent 3 — Supported SQL, Execution and Limitations

Milestones 1 (validation), 2 (SQLite execution) and 3 (`verify_result`, envelope verification)
are implemented.
Branch `feature/sql-verification`, rebased on `origin/main` (`3ddc4f8`), HEAD `0c4198e`. Date: 2026-10-24.
The verifier is **isolated**: `execute_query` does not call it, and it is not wired into Agent 1.

## Layout

```
sql-safety/
  backend/__init__.py
  backend/sql_validator.py        # validate_sql()
  backend/query_executor.py       # execute_query()
  backend/result_verifier.py      # verify_result()
  tests/test_sql_validator.py     # 123 tests
  tests/test_query_executor.py    #  77 tests
  tests/test_result_verifier.py   #  77 tests
  SUPPORTED_SQL.md
  README.md
```

Per-agent `backend/` directories match Agent 1's shipped layout (`ai-analyst/backend/…`);
each agent's package is imported by running pytest from inside its own directory. Collapsing these
into one root-level `backend/` is Agent 4's Gate 6 decision, not Agent 3's.

## Implemented

- `validate_sql(sql, dialect="sqlite") -> dict` — never raises; every outcome is a dict.
- `execute_query(sql, database_config) -> dict` — never raises; SQLite only, read-only.
- `verify_result(question, result) -> dict` — never raises; structural envelope checks only.
- `tests/` — **277 passing tests** (123 + 77 + 77), no live model or GPU needed. Execution tests
  build throwaway SQLite files under `tmp_path`; nothing here depends on Agent 2's sample database.

All three entry points fail closed: anything not explicitly recognised as a single read-only query,
as a well-formed configuration, or as a complete result envelope, is an error rather than an
attempt.

### Compatibility with Agent 1's repair loop

`ai-analyst/HANDOFF.md` injects the validator as
`validate_sql(sql, dialect) -> {"valid": bool, "error": {"type", "message"} | None}`. This module's
result is a superset of that shape — `valid` is a bool and `error` is exactly `{"type", "message"}`
or `None` — so `generate_validated_sql(question, schema, validate_sql)` can pass this function
directly. Dialect comes from `schema["dialect"]`, which Agent 1 defaults to `"sqlite"` — the same
default used here.

## Supported dialects

Validation and execution support **different sets**, and that gap is intentional.

| Requested | Parsed by `validate_sql` | Executed by `execute_query` |
|---|---|---|
| `sqlite`, `sqlite3` | yes | **yes** |
| `postgres`, `postgresql`, `pg` | yes | no — `unsupported_dialect` |
| `mysql`, `mariadb` | yes | no — `unsupported_dialect` |
| anything else (`bigquery`, `duckdb`, `tsql`, `snowflake`, `oracle`, `redshift`) | no — `unsupported_dialect` | no |

Dialect matching is case-insensitive on both sides. **Parsing a dialect is not supporting a
dialect**: Postgres and MySQL SQL is validated against the right grammar so Agent 1 gets useful
rejections early, but `execute_query` opens only SQLite and refuses the other dialects before it
touches a connection. Adding an engine requires both a `SUPPORTED_DIALECTS` entry (validation) and
a real driver path plus tests in `EXECUTION_DIALECTS` (execution) — nothing here claims support
that has not been implemented and tested.

## Accepted statement forms

Single statement only: `SELECT …`, `WITH … SELECT …`, set operations
(`UNION`, `UNION ALL`, `EXCEPT`, `INTERSECT`), and parenthesised subqueries. Projections,
filters, joins, `GROUP BY`/`HAVING`, `ORDER BY`/`LIMIT`/`OFFSET`, `DISTINCT`, `CASE`,
window functions, CTEs (including recursive ones), nested subqueries and ordinary
aggregate/scalar functions are allowed.

## Rejected

| `error.type` | Examples |
|---|---|
| `invalid_input` | non-string `sql` (None, int, bytes, list, dict) |
| `empty_query` | `""`, whitespace, comment-only input |
| `query_too_long` | input over `MAX_SQL_LENGTH` (20 000 chars) |
| `unsupported_dialect` | dialects outside the table above, non-string dialect |
| `syntax_error` | `SELCT 1`, `SELECT FROM`, `INTO OUTFILE`, unterminated strings |
| `multiple_statements` | `SELECT 1; SELECT 2`, `SELECT 1; DROP TABLE t`, `SELECT 1 -- x\n; DROP TABLE t` |
| `unsupported_statement` | INSERT/UPDATE/DELETE/MERGE, CREATE/DROP/ALTER/TRUNCATE, BEGIN/COMMIT/ROLLBACK, GRANT/REVOKE, SET/USE, VACUUM/PRAGMA/ATTACH/DETACH, CALL |
| `forbidden_construct` | `SELECT … FOR UPDATE` (locking), `WITH d AS (DELETE … RETURNING *) SELECT …` (write smuggled into a CTE), `SELECT * INTO t2 FROM t1` |
| `forbidden_function` | see denylist below |

Blocked node types are scanned across the **whole AST**, not just the root — the root-type check
alone would accept `WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d`.

## Denied functions

Denylist (case-insensitive, plus `dblink*`, `pg_read_*`, `pg_write_*`, `pg_ls_*`, `utl_*`,
`http_*`, `sys_*` prefixes): file access (`load_extension`, `readfile`, `pg_read_file`,
`lo_import`/`lo_export`), command execution (`xp_cmdshell`, `sp_executesql`, `sys_exec`),
time-based probing (`sleep`, `pg_sleep`, `BENCHMARK`), lock abuse (`get_lock`, `release_lock`),
`eval`, `dump`.

This is a denylist, **not** a complete defence — its purpose is to block well-known dangerous
calls, not to be the control that makes execution safe.

## Validator result shape

Both outcomes carry the same keys:

```python
{"status": "ok" | "error", "valid": bool, "normalized_sql": str | None,
 "dialect": str | None, "requested_dialect": Any,
 "statement_kind": "select" | "set_operation" | "subquery" | None,
 "tables": [str], "error": {"type", "message"} | None, "errors": [dict],
 "checks_passed": [str]}
```

- `error` uses the same `{"type", "message"}` shape as `backend/llm_client.LLMError.to_dict()`.
- `checks_passed` records the gates reached in order, so a rejection shows how far the query got.
- `normalized_sql` is regenerated by SQLGlot with comments stripped. The executor runs
  `normalized_sql`, never the raw caller string — that removes terminator/comment tricks from
  the executed text, and the executed text is what `execute_query` reports back in `sql`.
- `tables` is informational (referenced relations, CTE aliases included) for schema checks by
  Agents 1/2; it is not a security decision.
- Statuses are only `ok`/`error`. `empty` is a *result* property and belongs to the execution
  layer, consistent with Agent 1's `CONTRACT_PROPOSAL.md`.

## `execute_query` — interface

```python
execute_query(sql: str, database_config: dict) -> dict
```

`database_config` shape — **PROPOSED by Agent 3, NOT yet agreed in
`INTEGRATION_CONTRACT.md`.** The shared contract names the `database_config` parameter for
Agents 2, 3 and 4 but defines no keys for it. This is Agent 3's proposal; treat it as unratified
until Agents 2 and 4 sign off, and expect it to change if they propose something else:

```python
{"dialect": "sqlite",          # optional, defaults to "sqlite"; anything else is rejected
 "path": "/path/to/db.sqlite", # REQUIRED, no default location
 "timeout_s": 5,               # optional, DEFAULT_TIMEOUT_S=5, bounded 0.05–300
 "max_rows": 1000}             # optional, DEFAULT_MAX_ROWS=1000, bounded 1–100000 (int only)
```

Rules enforced by `_resolve_config`, all failing closed with `invalid_config` unless noted:

- the config must be a dict; `dialect` must be a string and, if given, must be `sqlite`
  (`unsupported_dialect` for a real dialect name);
- `path` must be a non-empty string, at most `MAX_PATH_LENGTH` (1024) characters, free of NUL
  bytes, and must already exist as a readable file: missing → `database_not_found`, a directory
  → `invalid_config`, unreadable → `database_not_readable`;
- `timeout_s` accepts `int`/`float` (not `bool`, not NaN/inf); `max_rows` accepts `int` only, so
  `10.5` and `True` are rejected rather than coerced;
- out-of-range numbers are rejected instead of silently clamped — a mistyped `max_rows: 0`
  cannot disable the row cap.

### Execution controls

1. Configuration is resolved **before** the query is validated, and validation happens **before**
   any file is opened. A rejected query never touches the database.
2. `validate_sql` is called inline inside `execute_query` (report §5 single-execution-path rule).
   Callers may validate first, but they cannot substitute for it.
3. The connection is `sqlite3.connect(<percent-encoded file: URI>?mode=ro, uri=True)`. Encoding is
   required because a raw `?` or `#` in a path would otherwise be read as URI options. `mode=ro`
   means SQLite refuses to create a missing file and refuses writes even if validation were
   bypassed — verified by a test that monkeypatches the validator to accept an `UPDATE`.
4. Rows are read with `fetchmany(max_rows + 1)` so truncation is *detected*, never guessed; at most
   `max_rows` rows are returned.

### Result envelope

Every outcome — success and each failure class — carries the same ten keys, in this order
(`ENVELOPE_KEYS`):

```python
{"status": "success" | "error",
 "sql": str | None,        # the normalized_sql that was (or would have been) run
 "dialect": str | None,
 "columns": [str], "rows": [[Any]], "row_count": int,
 "truncated": bool, "warnings": [str],
 "elapsed_ms": float, "error": {"type", "message"} | None}
```

- An empty result set is `status="success"`, `row_count: 0`, `error: None` — never an error
  (contract §2).
- `row_count` counts rows **returned**, so a truncated result reports `row_count == max_rows`.
  The number of rows the query *would* have produced is deliberately unknown; the executor does
  not run a second `COUNT(*)` to guess it.
- `truncated: true` always arrives with a matching warning naming the limit
  (`"Results were truncated to the first N rows; more matching rows exist but were not read."`),
  as contract §2 requires.
- `columns` comes from `cursor.description`; `rows` are plain lists, and SQLite values map to
  `int`, `float`, `str`, `bytes` or `None`.
- `elapsed_ms` is wall-clock for the whole call, including validation and connection setup.
- `execute_query` never raises for any input: driver, OS and URI errors are all mapped into the
  envelope.

### Error categories

`error.type` is one controlled value from this list; the message is a fixed string, at most
`MAX_MESSAGE_LENGTH` (200) characters. SQLite's own text is matched only to *classify* a failure
and is never copied into a response, so no raw exception, filesystem path or credential is
surfaced.

| `error.type` | Meaning |
|---|---|
| `invalid_config` | config or option is malformed, out of range, or names no file |
| `unsupported_dialect` | execution requested for a dialect other than SQLite |
| `database_not_found` | the configured file does not exist |
| `database_not_readable` | the file exists but cannot be read |
| `database_unavailable` | cannot be opened read-only, is not a SQLite database, or is locked by another writer |
| `invalid_sql` | rejected by `validate_sql`, or by the engine at run time (reason tokens `unknown_relation`, `unknown_column`, `syntax_error`) |
| `permission_denied` | the query needs write access; execution is read-only |
| `timeout` | the deadline was reached and the statement was interrupted |
| `execution_failed` | any other engine rejection, with no driver text leaked |

Validator rejections surface as `invalid_sql` carrying the validator's own reason token, e.g.
`SQL validation rejected the query (reason: multiple_statements)`.

## `verify_result` — interface and scope

```python
verify_result(question: str, result: dict) -> dict
```

`backend/result_verifier.py` checks **one** thing: whether an `execute_query` result envelope is
internally consistent and reports a successful read-only execution. Its verdict carries
`scope: "envelope_consistency"` so a caller can state which kind of evidence it holds.

```python
{"passed": bool,                  # the verdict callers should gate on
 "status": "success" | "error",   # did the verifier itself complete
 "scope": "envelope_consistency",
 "checks_passed": [str], "failures": [{"check", "reason", "message"}],
 "warnings": [str],
 "evidence": {"row_count", "columns", "truncated", "empty_result", "execution_status"},
 "explanation": str, "error": {"type", "message"} | None}
```

Every outcome carries all of `VERIFIER_KEYS`, in that order; the input dict is never mutated. When
the verifier cannot run at all, `status="error"` with `error.type` of `invalid_input` (a non-dict
`result` or non-string `question`) or `missing_result_fields` (any of the executor's
`ENVELOPE_KEYS` absent) — and `passed` is `False` in both cases. Callers should gate on `passed`
and `failures`: `checks_passed` records the gates the verifier *reached*, so a gate whose input was
malformed can be listed there without having been evaluated.

### What a pass establishes

- every required envelope field was present, with the right type, and `status` was a contract value;
- execution reported `success` with no `error` object, and `sql` names the statement that ran;
- `row_count` equals the rows actually present, and each row has exactly one value per column;
- an empty result set is treated as a successful outcome, not a failure;
- a truncated result discloses itself: `truncated: true` requires a matching warning and at least
  one row, and a truncation warning is refused when `truncated` is `false`;
- a result already carrying `verification: {"passed": false}` stays failed.

### What a pass does NOT establish

- **it does not show the query answered the user's question.** `question` is used only for input
  validation and is never compared against SQL, columns or rows. A wrong-but-well-formed query
  passes, and the passing explanation says so: structure only, not factual correctness;
- **no count/total/ranking evidence checks** (report §5 asks for them). Those figures live in
  Agent 1's `answer` text and in `chart`, neither of which `verify_result(question, result)`
  receives; adding them means a contract change, not a local decision;
- it does not re-run the query, compare a second source, or judge whether the data is true.

Fail-closed ordering matters: a non-`success` status is refused **before** any consistency work, so
no amount of internal tidiness can promote a failed execution (contract §5.5). Reason tokens are
`unexpected_status`, `error_object_malformed`, `error_present_on_success`, `sql_missing`,
`row_count_malformed`, `elapsed_ms_malformed`, `truncated_not_boolean`, `rows_malformed`,
`columns_malformed`, `warnings_malformed`, `dialect_malformed`, `row_count_mismatch`, `ragged_rows`,
`columns_missing_for_rows`, `truncation_warning_missing`, `truncation_warning_without_truncation`,
`truncated_but_empty`, `previous_verification_failed`, `verification_object_malformed`. Messages are
fixed strings built from field names, counts and reason tokens — never row or column values,
filesystem paths, credentials or driver text. `evidence["columns"]` is the one place column *names*
appear, as structural metadata; cell values never do.

**Not wired in.** `execute_query` does not call the verifier, and Agent 1 does not call it yet.
Contract §3 shows Agent 4 folding this verdict into `verification: {"passed": true}`, which
`passed` supports directly — but the contract does not yet say whether a *missing* `verification`
counts as passed or failed, and `ai-analyst/backend/agent.py:58-60` currently refuses only
on `passed is False`, i.e. it treats a missing verification as acceptable. Closing that gap is an
Agent 1/Agent 4 decision, not something Agent 3 changes unilaterally.

## Timeouts — what is and is not guaranteed

SQLite exposes no statement timeout through the standard-library driver
(`Connection.settimeout()` governs lock waits, not query duration). The deadline is implemented
with `set_progress_handler(_abort, 1000)`: the handler runs every 1000 virtual-machine steps and
returns non-zero once `time.monotonic()` passes `timeout_s`, which makes SQLite abort with an
interrupt.

Consequences, stated plainly:

- it is **cooperative, not a hard kill** — a step batch can overshoot the deadline by up to 1000
  instructions (roughly 0.06 s measured at `timeout_s=0.1`);
- it only fires while the engine is actually stepping. Because `fetchmany` stops pulling rows
  early, a capped read of an unbounded query can finish inside the deadline: `WITH RECURSIVE …
  SELECT x FROM t` with `max_rows=5` returns `success` + `truncated` rather than `timeout`. Work
  that cannot be short-circuited (`count(*)` over the same recursion) does time out, as tested;
- so `timeout_s` bounds duration *given the row cap*, and `max_rows` is the control that actually
  limits unbounded scans. Neither is a substitute for a real per-statement timeout, and this
  module does not claim one.

## Security boundary — what these controls do not cover

Per report §5, a parser allowlist plus row caps is not a complete security boundary:

1. **The file is trusted input.** Validation cannot stop a legitimate `SELECT` from reading
   whatever the connection can reach, and opening an attacker-supplied SQLite file carries its own
   risks (resource exhaustion, malformed internal structures). The controls here bound payload and
   duration; they do not make an untrusted database safe to open.
2. **No value interpolation** of untrusted input happens in this module — callers pass one
   complete SQL string, so there is no parameter-binding surface. Agents 1/4 must not build SQL by
   concatenating user text before calling here.
3. **No column- or row-level authorization** and no per-table allowlist: if the file is readable,
   every relation in it is readable. That is a permissions concern for whoever provisions the
   database.
4. **No statement-cost budgeting** beyond `timeout_s` and `max_rows`.
5. `PRAGMA`, `ATTACH`, `VACUUM` and extensions are rejected as SQL text; none of them is the
   mechanism keeping writes out — `mode=ro` is.

## Assumptions (documented, easy to change)

1. `dialect` defaults to `"sqlite"` in both modules; the report treats SQLite as the reproducible
   test database.
2. No `LIMIT` is injected at validation time; result caps are applied at fetch time by the
   executor.
3. `MAX_SQL_LENGTH = 20 000` chars is conservative; raise it only with a documented reason.
4. Every repaired query re-enters `validate_sql` (report §5 single-execution-path rule). Agent 1
   proposes Agent 4's orchestrator drives the ≤2-attempt repair loop; either way the validator
   cannot tell a first attempt from a repair, and `execute_query` re-validates regardless.
5. SQLGlot `>= 30` AST behaviour is relied upon (verified against `30.24.0` in these tests).
6. One statement per call, one connection per call, closed in `finally`; there is no pool and no
   session state. The cursor is not closed explicitly and a capped read leaves the statement
   unfinalised; `Connection.close()` releases both, verified by 50 executions under `-X dev` with
   no `ResourceWarning` and no error for outstanding rows.
7. Result cells are returned as-is. A very large `BLOB`/`TEXT` cell inside a permitted row is
   loaded into memory by `fetchmany` — cell-size bounding is **not** implemented.
8. "Never raises" holds for plain data (`str`, `bytes`, numbers, `None`, JSON-like dicts and
   lists), which is what the contract allows across agent boundaries. Objects with failing
   dunder methods (a mapping whose `get()` raises, a sequence whose `__len__`/`__iter__` raise)
   propagate from `_resolve_config` and `_check_consistency` instead of being mapped into an
   envelope; `validate_sql`'s round-trip re-parse is likewise outside its try/except.

## Verified behaviours worth knowing

- A WAL-mode database reads correctly through the read-only connection, including while another
  connection holds an open writer (tested with `live.db` plus `-wal`/`-shm` sidecars present).
  Not reproduced here, and still expected from SQLite's own semantics: a WAL file that needs
  recovery cannot be opened read-only, because recovery writes.
- Paths containing `#`, `?` or spaces work, because the path is percent-encoded into the URI.
- A zero-byte file is a valid empty SQLite database, not an error; only a file with content that
  is not a SQLite database yields `database_unavailable`.

## Dependency

`sqlglot>=30.0` was added to the shared root `requirements.txt` in milestone 1 (approved by the
repository owner before touching that shared file). Milestones 2 and 3 added **no dependency**:
execution uses the standard library `sqlite3`, and verification uses only the standard library.
Set up and run:

```
py -m venv .venv                                  # from the repository root
.venv/Scripts/python -m pip install -r requirements.txt
cd sql-safety
../.venv/Scripts/python -m pytest tests -q        # same command shape as Agent 1's README
```

The system `python` on this machine has no `pytest`; use the project venv as above.

## Not yet implemented

Integration, not implementation, is what is missing: `verify_result` exists but is not called by
`execute_query` or by Agent 1's `synthesize_answer`, and no end-to-end run is possible yet because
Agent 2's dataset and Agent 4's orchestrator are not in the repository. Also outstanding:
count/total/ranking evidence checks (they need `answer` text this interface does not receive),
PostgreSQL and MySQL execution, large-cell bounding, and the shared-contract decisions on
`database_config` and the `error.type` enum. Nothing here claims compatibility with modules that do
not exist yet.
