"""Schema-aware SQL generation (Agent 1).

generate_sql() interprets a natural-language question against the schema context
from Agent 2 and returns the contract shape agreed in CONTRACT_PROPOSAL.md:

    {"status": "ok" | "clarification_needed" | "error",
     "sql": str | None, "assumptions": [...],
     "clarification_question": str | None, "error": dict | None}

This module NEVER executes SQL. Generated SQL must pass Agent 3's validator
before anything touches the database.
"""
from __future__ import annotations

import re

from backend import llm_client
from backend.llm_client import LLMError

# Server context is 4096 tokens; keep the schema block well under that.
MAX_SCHEMA_CONTEXT_CHARS = 5000

SQL_KEYWORDS = {
    "select", "from", "where", "and", "or", "not", "in", "is", "null", "as",
    "on", "join", "inner", "left", "right", "outer", "cross", "group", "by",
    "order", "having", "limit", "offset", "distinct", "case", "when", "then",
    "else", "end", "asc", "desc", "between", "like", "ilike", "exists", "union",
    "all", "any", "with", "recursive", "insert", "update", "delete", "into",
    "values", "set", "table", "true", "false", "count", "sum", "avg", "min",
    "max", "abs", "round", "coalesce", "nullif", "cast", "upper", "lower",
    "trim", "length", "substr", "substring", "concat", "date", "year",
    "month", "day", "total",
}

SYSTEM_PROMPT = """You are AskBase's SQL analyst. Convert the user's question into ONE read-only SQL query for the given database schema.

Rules:
- Use ONLY the tables, columns and relationships shown in the schema. Never invent names, currencies, units or time periods.
- Prefer clear aggregations and joins implied by the schema. Add ORDER BY for ranking questions.
- Ask ONE specific clarifying question (status "clarification_needed", sql null) when the question is MATERIALLY ambiguous:
  * no time period given for a time-sensitive metric (e.g. "recent", "last quarter", undefined range on a date column),
  * the requested metric or its meaning does not map to any column (e.g. "profit" when only cost and price exist),
  * an entity/join choice with multiple defensible meanings (e.g. "customers" could be accounts or people),
  * aggregation basis unclear (per order vs per customer) when both change the answer materially.
- Choose a sensible default and record it in "assumptions" ONLY for immaterial choices: sort direction, tie-breaking, result limit, column naming.
- If the schema has no way to answer at all, status "ok" with the closest SELECT and explain the gap in assumptions, or ask for clarification.
- Respond with ONLY a JSON object, no prose:
{"status": "ok" | "clarification_needed", "sql": "<one SELECT statement>" | null, "assumptions": ["..."], "clarification_question": "<question>" | null}
"""


def schema_name_index(schema: dict) -> dict:
    """Lowercased table/column lookup built from Agent 2's schema dict."""
    tables = {}
    for table in schema.get("tables", []):
        name = table.get("name", "")
        cols = {str(c.get("name", "")).lower() for c in table.get("columns", []) if c.get("name")}
        tables[name.lower()] = cols
    return {"tables": tables,
            "columns": {c for cols in tables.values() for c in cols}}


def format_schema_context(schema: dict) -> str:
    """Compact, token-bounded schema text for the prompt."""
    lines = []
    for table in schema.get("tables", []):
        parts = []
        for col in table.get("columns", []):
            flag = " PK" if col.get("pk") else ""
            fk = f" FK->{col['fk_to']}" if col.get("fk_to") else ""
            parts.append(f"{col.get('name')} {col.get('type', '')}{flag}{fk}".strip())
        lines.append(f"TABLE {table.get('name')} ({', '.join(parts)})")
    for warning in schema.get("warnings", []):
        lines.append(f"WARNING: {warning}")
    text = "\n".join(lines)
    if len(text) > MAX_SCHEMA_CONTEXT_CHARS:
        text = text[:MAX_SCHEMA_CONTEXT_CHARS] + "\n[… schema truncated …]"
    return text


def _strip_string_literals(sql: str) -> str:
    return re.sub(r"'[^']*'", "''", sql)


_NON_ALIAS_WORDS = SQL_KEYWORDS | {"where", "on", "group", "order", "limit", "having",
                                   "inner", "left", "right", "cross", "using"}


def find_unknown_identifiers(sql: str, schema: dict) -> list[str]:
    """Identifiers in the SQL that are neither schema names, SQL keywords,
    nor query-local aliases. Guards against invented columns/tables."""
    index = schema_name_index(schema)
    known = set(index["tables"]) | set(index["columns"])
    stripped = _strip_string_literals(sql)
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", stripped)
    lowered = [t.lower() for t in tokens]

    aliases = set()
    for i, tok in enumerate(lowered):
        if tok == "as" and i + 1 < len(lowered):
            aliases.add(lowered[i + 1])
        elif tok in ("from", "join") and i + 2 < len(lowered):
            nxt = lowered[i + 2]
            if nxt == "as" and i + 3 < len(lowered):
                aliases.add(lowered[i + 3])
            elif nxt not in _NON_ALIAS_WORDS:
                aliases.add(nxt)

    # tokens used as a qualified prefix (alias.table before a dot) are covered
    # by the column check on the right side
    prefixes = {m.lower() for m in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\.", stripped)}

    unknown, seen = [], set()
    for tok in tokens:
        low = tok.lower()
        if low in SQL_KEYWORDS or low in known or low in aliases or low in prefixes:
            continue
        if low not in seen:
            seen.add(low)
            unknown.append(tok)
    return unknown


def generate_sql(question: str, schema: dict, prior_error: dict | None = None) -> dict:
    """Produce contract output for one generation (or repair) attempt."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    context = format_schema_context(schema)
    user = f"Schema:\n{context}\n\nQuestion: {question}"
    if prior_error:
        user += (f"\n\nYour previous attempt failed validation/execution with: "
                 f"{prior_error.get('type', 'error')}: {prior_error.get('message', '')}\n"
                 f"Fix the query and try again.")
    messages.append({"role": "user", "content": user})

    try:
        raw = llm_client.complete_json(messages)
    except LLMError as exc:
        return {"status": "error", "sql": None, "assumptions": [],
                "clarification_question": None, "error": exc.to_dict()}

    status = raw.get("status")
    if status not in ("ok", "clarification_needed"):
        return {"status": "error", "sql": None, "assumptions": [],
                "clarification_question": None,
                "error": {"type": "malformed", "message": f"Unexpected status: {status!r}"}}

    if status == "clarification_needed":
        q = raw.get("clarification_question")
        if not q:
            return {"status": "error", "sql": None, "assumptions": [],
                    "clarification_question": None,
                    "error": {"type": "malformed",
                              "message": "clarification_needed without clarification_question"}}
        return {"status": "clarification_needed", "sql": None,
                "assumptions": _as_list(raw.get("assumptions")),
                "clarification_question": str(q), "error": None}

    sql = raw.get("sql")
    if not sql or not str(sql).strip():
        return {"status": "error", "sql": None, "assumptions": [],
                "clarification_question": None,
                "error": {"type": "malformed", "message": "status ok but no SQL returned"}}

    unknown = find_unknown_identifiers(str(sql), schema)
    if unknown:
        return {"status": "error", "sql": str(sql), "assumptions": _as_list(raw.get("assumptions")),
                "clarification_question": None,
                "error": {"type": "invented_identifier",
                          "message": f"Not in schema: {', '.join(unknown)}"}}

    return {"status": "ok", "sql": str(sql),
            "assumptions": _as_list(raw.get("assumptions")),
            "clarification_question": None, "error": None}


def _as_list(value) -> list:
    return [str(v) for v in value] if isinstance(value, list) else []
