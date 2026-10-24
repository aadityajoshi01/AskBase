"""Read-only SQL validator — Agent 3 safety boundary (report §5).

`validate_sql` never raises: every outcome is a dict with `status` "ok" or "error"
and a machine-readable `error.type`, so callers cannot mistake a rejected query for
an accepted one. It fails closed — unknown input, unknown dialect, unparsable SQL,
unexpected parser behaviour and anything outside the read-only allowlist are errors.

Dialects are an explicit allowlist (see SUPPORTED_DIALECTS); other syntaxes are
rejected rather than parsed with a permissive fallback.

Parsing alone is NOT the security boundary. Execution must still use a read-only
connection/role with timeouts and row limits (backend/query_executor.py, milestone 2).
"""
from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp

SUPPORTED_DIALECTS: dict[str, str] = {
    "sqlite": "sqlite",
    "sqlite3": "sqlite",
    "postgres": "postgres",
    "postgresql": "postgres",
    "pg": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
}

MAX_SQL_LENGTH = 20000
MAX_MESSAGE_LENGTH = 200

_ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Except, exp.Intersect, exp.Subquery, exp.Paren)

# Rejected anywhere in the AST, not only at the root: a CTE body or an INTO/LOCK
# clause inside an otherwise valid SELECT must not smuggle a write through.
_BLOCKED_NODE_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Upsert", "LoadData", "Copy", "Export",
    "Create", "Drop", "Alter", "AlterColumn", "AlterIndex", "Rename", "TruncateTable",
    "Transaction", "Commit", "Rollback", "Savepoint", "Set", "SetItem", "Use",
    "Grant", "Revoke", "Pragma", "Attach", "Detach", "Analyze", "Vacuum", "Refresh",
    "Cache", "Command", "Kill", "Lock", "Into", "IntoPlaceholder", "Returning",
    "StoredProcedure", "Execute",
)

# Denylist, not exhaustive: defence in depth requires a read-only, non-superuser
# connection so these cannot be reached even if a name slips through.
_BLOCKED_FUNCTIONS = {
    "load_extension", "enable_load_extension", "sqlite_exec", "eval", "system",
    "shell", "execute_shell", "readfile", "writefile", "dump",
    "load_file", "loadfile", "into_outfile", "sleep", "benchmark", "get_lock",
    "release_lock", "is_free_lock", "is_used_lock", "master_query", "sys_exec",
    "sys_eval", "xp_cmdshell", "sp_executesql", "openrowset", "openquery",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "pg_sleep",
    "pg_terminate_backend", "pg_reload_conf", "pg_switch_wal", "pg_create_restore_point",
    "lo_import", "lo_export", "dblink_exec", "query", "utl_http", "httpurity",
}

_BLOCKED_FUNCTIONS_PREFIXES = ("dblink", "pg_write_", "pg_read_", "pg_ls_", "utl_", "http_", "sys_")

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*")


def _blocked_types() -> tuple[type, ...]:
    found = []
    for name in _BLOCKED_NODE_NAMES:
        node = getattr(exp, name, None)
        if isinstance(node, type) and issubclass(node, exp.Expression):
            found.append(node)
    return tuple(found)


_BLOCKED_TYPES = _blocked_types()


def _fail(error_type: str, message: str, checks: list[str], dialect: str | None,
          requested: Any) -> dict:
    error = {"type": error_type, "message": str(message)[:MAX_MESSAGE_LENGTH]}
    return {
        "status": "error",
        "valid": False,
        "normalized_sql": None,
        "dialect": dialect,
        "requested_dialect": requested,
        "statement_kind": None,
        "tables": [],
        "error": error,
        "errors": [error],
        "checks_passed": checks,
    }


def _ok(normalized: str, checks: list[str], root: exp.Expression, kind: str,
        dialect: str, requested: Any) -> dict:
    tables = sorted({t.sql(dialect=dialect) for t in root.find_all(exp.Table)})
    return {
        "status": "ok",
        "valid": True,
        "normalized_sql": normalized,
        "dialect": dialect,
        "requested_dialect": requested,
        "statement_kind": kind,
        "tables": tables,
        "error": None,
        "errors": [],
        "checks_passed": checks,
    }


def _statement_kind(root: exp.Expression) -> str:
    if isinstance(root, (exp.Except, exp.Intersect)) or isinstance(root, exp.Union):
        return "set_operation"
    if isinstance(root, (exp.Subquery, exp.Paren)):
        return "subquery"
    return "select"


def _resolve_dialect(raw: Any, checks: list[str]) -> tuple[str | None, dict | None]:
    if not isinstance(raw, str) or not raw.strip():
        return None, _fail("unsupported_dialect", "dialect must be a non-empty string",
                           checks, None, raw)
    canonical = SUPPORTED_DIALECTS.get(raw.strip().casefold())
    if canonical is None:
        supported = ", ".join(sorted(set(SUPPORTED_DIALECTS.values())))
        return None, _fail("unsupported_dialect",
                           f"dialect {raw!r} is not supported; supported: {supported}",
                           checks, None, raw)
    checks.append("dialect_supported")
    return canonical, None


def _function_names(node: exp.Expression, dialect: str) -> list[str]:
    names: list[str] = []
    raw_name = getattr(node, "name", None)
    if isinstance(raw_name, str) and raw_name:
        names.append(raw_name)
    rendered = node.sql(dialect=dialect, comments=False)
    match = _NAME_RE.match(rendered.strip())
    if match:
        names.append(match.group(0))
    return names


def _forbidden_function(root: exp.Expression, dialect: str) -> str | None:
    for node in root.find_all(exp.Func):
        for name in _function_names(node, dialect):
            lowered = name.casefold()
            if lowered in _BLOCKED_FUNCTIONS or any(
                lowered.startswith(prefix) for prefix in _BLOCKED_FUNCTIONS_PREFIXES
            ):
                return name
    return None


def validate_sql(sql: Any, dialect: Any = "sqlite") -> dict:
    """Validate one read-only query; return a contract-shaped result dict.

    Accepts SELECT / WITH-SELECT / set operations / parenthesised subqueries.
    Rejects non-strings, empty or comment-only input, oversized payloads, unsupported
    dialects, syntax errors, multiple statements, writes, DDL, transaction control,
    locking, SELECT ... INTO, and denied functions.
    """
    checks: list[str] = []

    if not isinstance(sql, str):
        return _fail("invalid_input", "sql must be a string", checks, None, dialect)
    if not sql.strip():
        return _fail("empty_query", "sql is empty", checks, None, dialect)
    checks.append("input_is_text")

    if len(sql) > MAX_SQL_LENGTH:
        return _fail("query_too_long",
                     f"sql exceeds {MAX_SQL_LENGTH} characters", checks, None, dialect)
    checks.append("length_bounded")

    canonical, failure = _resolve_dialect(dialect, checks)
    if failure:
        return failure

    try:
        # exp.Semicolon is sqlglot's placeholder for an empty statement (e.g. a comment
        # after the terminator); it carries nothing executable and must not be counted
        # as a second statement.
        parsed = [node for node in sqlglot.parse(sql, dialect=canonical)
                  if node is not None and not isinstance(node, exp.Semicolon)]
    except Exception as exc:  # ParseError, TokenError, sqlglot internals
        return _fail("syntax_error", f"SQL could not be parsed: {exc}", checks, canonical, dialect)
    checks.append("parses")

    if not parsed:
        return _fail("empty_query", "sql contains no statement (empty or comment only)",
                     checks, canonical, dialect)
    if len(parsed) > 1:
        return _fail("multiple_statements",
                     f"exactly one statement is allowed, found {len(parsed)}",
                     checks, canonical, dialect)
    checks.append("single_statement")

    root = parsed[0]
    if not isinstance(root, _ALLOWED_ROOTS):
        return _fail("unsupported_statement",
                     f"{type(root).__name__} is not a read-only query; only SELECT, "
                     f"WITH-SELECT, set operations and subqueries are allowed",
                     checks, canonical, dialect)
    checks.append("read_only_statement_type")

    for node in root.walk():
        if isinstance(node, _BLOCKED_TYPES):
            return _fail("forbidden_construct",
                         f"{type(node).__name__} is not allowed in a read-only query",
                         checks, canonical, dialect)
    checks.append("no_forbidden_constructs")

    forbidden = _forbidden_function(root, canonical)
    if forbidden:
        return _fail("forbidden_function",
                     f"function {forbidden!r} is not allowed", checks, canonical, dialect)
    checks.append("no_forbidden_functions")

    try:
        normalized = root.sql(dialect=canonical, comments=False)
    except Exception as exc:
        return _fail("internal_error", f"could not regenerate SQL: {exc}",
                     checks, canonical, dialect)
    if not normalized or not normalized.strip():
        return _fail("internal_error", "regenerated SQL is empty", checks, canonical, dialect)
    if len(normalized) > MAX_SQL_LENGTH:
        return _fail("query_too_long", "normalized SQL exceeds the size limit",
                     checks, canonical, dialect)
    if not isinstance(sqlglot.parse_one(normalized, dialect=canonical), _ALLOWED_ROOTS):
        return _fail("internal_error", "normalized SQL is not a read-only query",
                     checks, canonical, dialect)
    checks.append("normalized_sql_revalidated")

    return _ok(normalized, checks, root, _statement_kind(root), canonical, dialect)
