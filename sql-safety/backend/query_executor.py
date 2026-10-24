"""Restricted read-only query execution (Agent 3, milestone 2).

Scope: the contract-facing `execute_query()` envelope, `database_config`
validation, the mandatory call into Agent 3's own `validate_sql`, and SQLite
execution of the validator's `normalized_sql` over a read-only connection.

Execution controls (report §5 layered controls):
- `sqlite3.connect(..., uri=True)` with `mode=ro`, so the file is never created
  and writes fail at the engine level even if the validator were bypassed;
- a wall-clock deadline enforced through `set_progress_handler`, because the
  stdlib driver exposes no statement timeout (`settimeout` covers lock waits only);
- `fetchmany(max_rows + 1)` to detect truncation while never returning more than
  `max_rows` rows.

Parsing, the progress handler and the row cap bound the *payload and duration* of a
query; they do not make an untrusted database safe to open. The connection is still
only as trustworthy as the file Agent 2 hands it.

Expected `database_config` (SQLite only in this milestone):

    {"dialect": "sqlite",
     "path": "/path/to/database.db",
     "timeout_s": 5,
     "max_rows": 1000}

- `dialect` is optional and defaults to "sqlite"; any other value is rejected
  rather than attempted, matching the validator's explicit allowlist style.
- `path` is required. There is no default database location.
- `timeout_s` and `max_rows` are optional with DEFAULT_TIMEOUT_S / DEFAULT_MAX_ROWS.
  Both are bounded so a mistyped value fails closed instead of disabling the cap.

Public error messages are always fixed, controlled strings: they carry an
`error.type` and a reason token taken from a known enum, never raw exception text,
filesystem paths, connection strings or credentials.
"""
from __future__ import annotations

import math
import os
import sqlite3
import time
from pathlib import Path

from backend.sql_validator import MAX_MESSAGE_LENGTH, validate_sql
from backend.type_guards import check_query_types

EXECUTION_DIALECTS: frozenset[str] = frozenset({"sqlite"})

DEFAULT_TIMEOUT_S = 5
DEFAULT_MAX_ROWS = 1000

MIN_TIMEOUT_S = 0.05
MAX_TIMEOUT_S = 300
MIN_MAX_ROWS = 1
MAX_MAX_ROWS = 100_000
MAX_PATH_LENGTH = 1024

# Keys every execute_query result carries, in this order, for both outcomes.
ENVELOPE_KEYS = (
    "status", "sql", "dialect", "columns", "rows", "row_count",
    "truncated", "warnings", "elapsed_ms", "error",
)

PROGRESS_STEPS = 1000  # VDBE instructions between deadline checks

# Substrings of sqlite's own messages, used only to CLASSIFY a failure into a
# controlled reason token. The matched text is never copied into a response.
_OPEN_MARKERS = ("unable to open", "cannot open")
_READONLY_MARKERS = ("readonly", "read-only", "attempt to write", "authorization")
_BUSY_MARKERS = ("locked", "busy")
_CORRUPT_MARKERS = ("not a database", "file is not a database",
                    "database disk image is malformed")
_INTERRUPT_MARKERS = ("interrupt",)
_MISSING_RELATION_MARKERS = ("no such table", "no such view")
_MISSING_COLUMN_MARKERS = ("no such column", "no such function",
                           "no such aggregate", "has no column named")
_SYNTAX_MARKERS = ("syntax error", "incomplete input", "unrecognized token",
                   "wrong number of arguments")


def _envelope(status: str, *, sql: str | None = None, dialect: str | None = None,
              columns: list | None = None, rows: list | None = None,
              row_count: int = 0, truncated: bool = False,
              warnings: list[str] | None = None, elapsed_ms: float = 0.0,
              error: dict | None = None) -> dict:
    """Build a result dict that always carries every ENVELOPE_KEYS entry."""
    return {
        "status": status,
        "sql": sql,
        "dialect": dialect,
        "columns": list(columns or []),
        "rows": list(rows or []),
        "row_count": row_count,
        "truncated": truncated,
        "warnings": list(warnings or []),
        "elapsed_ms": elapsed_ms,
        "error": error,
    }


def _failure(error_type: str, message: str, *, sql: str | None = None,
             dialect: str | None = None, warnings: list[str] | None = None,
             elapsed_ms: float = 0.0) -> dict:
    return _envelope("error", sql=sql, dialect=dialect, warnings=warnings,
                     elapsed_ms=elapsed_ms,
                     error={"type": error_type,
                            "message": str(message)[:MAX_MESSAGE_LENGTH]})


def _resolve_dialect(config: dict, checks: list[str]):
    """Return (canonical dialect, None) or (None, failure kwargs)."""
    raw = config.get("dialect", "sqlite")
    if raw is None:
        checks.append("dialect_defaulted")
        return "sqlite", None
    if not isinstance(raw, str):
        return None, ("invalid_config", "database_config.dialect must be a string")
    canonical = raw.strip().casefold()
    if canonical not in EXECUTION_DIALECTS:
        supported = ", ".join(sorted(EXECUTION_DIALECTS))
        return None, ("unsupported_dialect",
                      f"only these dialects can be executed: {supported}")
    checks.append("dialect_supported")
    return canonical, None


def _resolve_path(config: dict, checks: list[str]):
    """Return (path, None) or (None, failure kwargs). Never echoes the path back."""
    raw = config.get("path")
    if raw is None:
        return None, ("invalid_config", "database_config.path is required")
    if not isinstance(raw, str) or not raw.strip():
        return None, ("invalid_config", "database_config.path must be a non-empty string")
    if len(raw) > MAX_PATH_LENGTH or "\x00" in raw:
        return None, ("invalid_config", "database_config.path is not a usable path")
    if not os.path.exists(raw):
        return None, ("database_not_found",
                      "the configured database file does not exist")
    if not os.path.isfile(raw):
        return None, ("invalid_config", "database_config.path does not name a file")
    if not os.access(raw, os.R_OK):
        return None, ("database_not_readable",
                      "the configured database file is not readable")
    checks.append("path_resolved")
    return raw, None


def _resolve_number(config: dict, key: str, default: int | float,
                    lower: float, upper: float, integer: bool, checks: list[str]):
    """Return (value, None) or (None, failure kwargs) for a bounded numeric option."""
    raw = config.get(key, default)
    if isinstance(raw, bool):
        return None, ("invalid_config", f"database_config.{key} must be a number")
    if integer:
        if not isinstance(raw, int):
            return None, ("invalid_config", f"database_config.{key} must be an integer")
    elif not isinstance(raw, (int, float)):
        return None, ("invalid_config", f"database_config.{key} must be a number")
    if isinstance(raw, float) and not math.isfinite(raw):
        return None, ("invalid_config", f"database_config.{key} must be finite")
    if not lower <= raw <= upper:
        return None, ("invalid_config",
                      f"database_config.{key} must be between {lower} and {upper}")
    checks.append(f"{key}_bounded")
    return raw, None


def _resolve_config(database_config) -> tuple[dict | None, tuple | None]:
    """Validate database_config without executing anything.

    Returns (normalized config, None) or (None, (error_type, message, checks)).
    """
    checks: list[str] = []
    if not isinstance(database_config, dict):
        return None, ("invalid_config", "database_config must be an object", checks)
    checks.append("config_is_object")

    dialect, failure = _resolve_dialect(database_config, checks)
    if failure:
        return None, (*failure, checks)

    path, failure = _resolve_path(database_config, checks)
    if failure:
        return None, (*failure, checks)

    timeout_s, failure = _resolve_number(database_config, "timeout_s", DEFAULT_TIMEOUT_S,
                                         MIN_TIMEOUT_S, MAX_TIMEOUT_S, False, checks)
    if failure:
        return None, (*failure, checks)

    max_rows, failure = _resolve_number(database_config, "max_rows", DEFAULT_MAX_ROWS,
                                        MIN_MAX_ROWS, MAX_MAX_ROWS, True, checks)
    if failure:
        return None, (*failure, checks)

    return {"dialect": dialect, "path": path,
            "timeout_s": timeout_s, "max_rows": max_rows}, None


def _file_uri(path: str) -> str:
    """Percent-encoded file: URI for a read-only connection.

    `mode=ro` makes SQLite refuse to create the file and refuse writes; encoding
    matters because a raw '?' or '#' in a path would otherwise be parsed as URI
    options rather than part of the filename.
    """
    return Path(path).resolve().as_uri() + "?mode=ro"


def _classify(exc: sqlite3.Error) -> tuple[str, str]:
    """Map a driver exception to (error.type, message) without quoting the driver."""
    text = str(exc).casefold()

    def hit(markers) -> bool:
        return any(marker in text for marker in markers)

    if isinstance(exc, sqlite3.OperationalError) and hit(_INTERRUPT_MARKERS):
        return "timeout", "query exceeded the configured timeout_s and was interrupted"
    if hit(_CORRUPT_MARKERS):
        return ("database_unavailable",
                "the configured file is not a readable SQLite database")
    if hit(_OPEN_MARKERS) or isinstance(exc, sqlite3.Warning):
        return ("database_unavailable",
                "the configured database could not be opened read-only")
    if hit(_READONLY_MARKERS):
        return ("permission_denied",
                "the query requires write access; execution is read-only")
    if hit(_BUSY_MARKERS):
        return ("database_unavailable", "the database is locked by another writer")
    if hit(_MISSING_RELATION_MARKERS):
        return ("invalid_sql",
                "SQL execution failed (reason: unknown_relation); the query "
                "references a table or view that does not exist")
    if hit(_MISSING_COLUMN_MARKERS):
        return ("invalid_sql",
                "SQL execution failed (reason: unknown_column); the query "
                "references a column or function that does not exist")
    if hit(_SYNTAX_MARKERS):
        return ("invalid_sql", "SQL execution failed (reason: syntax_error)")
    return ("execution_failed",
            "SQL execution failed (reason: engine_rejected_query)")


def _run_query(config: dict, normalized: str, started: float):
    """Execute validated SQL read-only. Returns (envelope_dict, None) or (None, failure)."""
    deadline = time.monotonic() + config["timeout_s"]

    def _abort() -> int:
        return 1 if time.monotonic() > deadline else 0

    conn = None
    try:
        conn = sqlite3.connect(_file_uri(config["path"]), uri=True)
        conn.set_progress_handler(_abort, PROGRESS_STEPS)
        cursor = conn.cursor()
        cursor.execute(normalized)

        fetched = cursor.fetchmany(config["max_rows"] + 1)
        truncated = len(fetched) > config["max_rows"]
        rows = [list(row) for row in fetched[:config["max_rows"]]]
        columns = [str(row[0]) for row in cursor.description or []]

        warnings: list[str] = []
        if truncated:
            warnings.append(
                f"Results were truncated to the first {config['max_rows']} rows; "
                f"more matching rows exist but were not read.")

        return _envelope("success", sql=normalized, dialect=config["dialect"],
                         columns=columns, rows=rows, row_count=len(rows),
                         truncated=truncated, warnings=warnings,
                         elapsed_ms=round((time.perf_counter() - started) * 1000, 3)
                         ), None
    except (sqlite3.Error, sqlite3.Warning) as exc:
        error_type, message = _classify(exc)
        return None, (error_type, message)
    except (OSError, ValueError):
        # Path/URI handling failures: controlled text, never the OS message.
        return None, ("database_unavailable",
                      "the configured database could not be opened read-only")
    finally:
        if conn is not None:
            try:
                conn.set_progress_handler(None, 0)
            except sqlite3.Error:
                pass
            try:
                conn.close()
            except sqlite3.Error:
                pass


def execute_query(sql: str, database_config: dict) -> dict:
    """Run one validated read-only SQLite query. Never raises.

    Contract: `execute_query(sql, database_config) -> dict` (INTEGRATION_CONTRACT.md
    §1). Status vocabulary is `success` / `error`; an empty result set is a `success`
    with `row_count: 0`, never an error (§2). `sql` carries the validator's
    `normalized_sql` — the exact text that was run, not the caller's raw string
    (SUPPORTED_SQL.md). `row_count` counts rows *returned*, so a truncated result
    reports `row_count == max_rows` with `truncated: true`.
    """
    started = time.perf_counter()

    def elapsed() -> float:
        return round((time.perf_counter() - started) * 1000, 3)

    config, failure = _resolve_config(database_config)
    if failure:
        error_type, message, _ = failure
        return _failure(error_type, message, elapsed_ms=elapsed())

    dialect = config["dialect"]

    # Single execution path (report §5): nothing may reach the database without
    # passing this validation call, so it stays inline here rather than trusting
    # the caller to have validated. Validation also finishes before any file is
    # opened, so a rejected query never touches the database.
    verdict = validate_sql(sql, dialect)
    if not verdict.get("valid"):
        reason = str((verdict.get("error") or {}).get("type") or "unknown")
        return _failure("invalid_sql",
                        f"SQL validation rejected the query (reason: {reason})",
                        dialect=dialect, elapsed_ms=elapsed())

    normalized = verdict.get("normalized_sql")
    if not normalized:
        return _failure("invalid_sql",
                        "SQL validation rejected the query (reason: empty_normalized)",
                        dialect=dialect, elapsed_ms=elapsed())

    # Runtime-type guard: SUM/AVG over columns that actually store non-numeric
    # TEXT is refused (it would silently compute zeros), and numeric TEXT under
    # MIN/MAX/ORDER BY is disclosed as a lexicographic-order warning.
    try:
        blocking, type_warnings = check_query_types(normalized, config["path"])
    except Exception:
        blocking, type_warnings = [], ["type guard skipped: the check itself failed"]
    if blocking:
        return _failure("type_unsafe_aggregate", "; ".join(blocking),
                        sql=normalized, dialect=dialect, elapsed_ms=elapsed())

    success, failure = _run_query(config, normalized, started)
    if failure:
        error_type, message = failure
        return _failure(error_type, message, sql=normalized, dialect=dialect,
                        elapsed_ms=elapsed())
    success["warnings"].extend(type_warnings)
    return success
