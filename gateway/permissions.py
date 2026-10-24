"""Roles, row limits and column masks (AskBase Phase 2, part 1).

The claim being enforced is per-user, so it lives on the server: a caller names
itself, the gateway resolves a role, and the role's policy decides which columns
the model is even told about and which rows the query can reach.

Two rules make this worth trusting:

* masking is subtractive — a column the role may not read is removed from the
  schema before generation, so the model cannot quote it, and a predicate is
  compiled into the query before execution, so it cannot read it either;
* policy is data, not SQL. Row filters are structured specs compiled through
  SQLGlot's expression builders with regex-validated identifiers and converted
  literals, so a policy file (or anything derived from a request) can never
  smuggle a statement fragment into the executed SQL.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from sqlglot import exp

POLICIES_FILE = Path(__file__).resolve().parent / "policies.json"

ROLES = ("admin", "analyst", "viewer")
DEFAULT_ROLE = "viewer"          # an unknown or absent identity gets the smallest claim
MAX_PREDICATE_DEPTH = 4          # semi-joins nest; a deeper chain is a malformed policy

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COMPARISONS = {"eq": exp.EQ, "neq": exp.NEQ, "gt": exp.GT,
                "gte": exp.GTE, "lt": exp.LT, "lte": exp.LTE}
_PREDICATE_KEYS = {"column", "in", "like", "table", "semi_join", "all_of", "any_of",
                   *set(_COMPARISONS)}


class PolicyError(ValueError):
    """A policy that cannot be enforced safely. Always fails the request closed."""


# --------------------------------------------------------------------------- load

def load_policies(path: Path = POLICIES_FILE) -> dict:
    """Read the policy file. A missing or unreadable file means role defaults only,
    which is still a restriction — never an unrestricted pass-through."""
    try:
        policies = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"users": {}, "role_defaults": {}, "datasets": {}}
    if not isinstance(policies, dict):
        return {"users": {}, "role_defaults": {}, "datasets": {}}
    for key in ("users", "role_defaults", "datasets"):
        if not isinstance(policies.get(key), dict):
            policies[key] = {}
    return policies


def resolve_principal(identity, policies: dict) -> dict:
    """Map a caller-supplied identity to a role. Unknown identities are a viewer,
    and a user record naming a role that does not exist is also a viewer."""
    name = str(identity or "").strip()
    record = policies.get("users", {}).get(name)
    role = record.get("role") if isinstance(record, dict) else None
    if role not in ROLES:
        role = DEFAULT_ROLE
    return {"user": name or "anonymous", "role": role,
            "label": (record or {}).get("label", name or "anonymous"),
            "known_user": bool(record)}


# ------------------------------------------------------------------------ policy

def policy_for(policies: dict, dataset_id: str, role: str) -> dict:
    """The effective policy for one (dataset, role) pair."""
    if role not in ROLES:
        raise PolicyError(f"unknown role: {role!r}")
    defaults = policies.get("role_defaults", {}).get(role, {}) or {}
    scoped = policies.get("datasets", {}).get(dataset_id, {}).get(role, {}) or {}
    if not isinstance(defaults, dict) or not isinstance(scoped, dict):
        raise PolicyError(f"policy for {dataset_id}/{role} must be an object")
    merged = {**defaults, **{k: v for k, v in scoped.items() if k != "mask_name_patterns"}}
    patterns = list(dict.fromkeys(list(defaults.get("mask_name_patterns", [])) +
                                 list(scoped.get("mask_name_patterns", []))))
    return {
        "dataset": dataset_id,
        "role": role,
        "unrestricted": bool(merged.get("unrestricted")),
        "row_filters": dict(merged.get("row_filters") or {}),
        "masked_columns": list(merged.get("masked_columns") or []),
        "mask_name_patterns": patterns,
        "max_rows": merged.get("max_rows"),
    }


def _mask_matchers(policy: dict) -> list[re.Pattern]:
    matchers = []
    for pattern in policy.get("mask_name_patterns", []):
        try:
            matchers.append(re.compile(str(pattern), re.IGNORECASE))
        except re.error as exc:
            raise PolicyError(f"bad mask pattern {pattern!r}: {exc}") from exc
    return matchers


def masked_columns(schema: dict, policy: dict) -> set[str]:
    """`table.column` names this policy hides, from explicit entries and name patterns."""
    if policy.get("unrestricted"):
        return set()
    explicit = {str(name).casefold() for name in policy.get("masked_columns", [])}
    matchers = _mask_matchers(policy)
    hidden = set()
    for table in schema.get("tables", []):
        table_name = str(table.get("name", ""))
        for column in table.get("columns", []):
            column_name = str(column.get("name", ""))
            qualified = f"{table_name}.{column_name}".casefold()
            if qualified in explicit or column_name.casefold() in explicit:
                hidden.add(f"{table_name}.{column_name}")
            elif any(matcher.search(column_name) for matcher in matchers):
                hidden.add(f"{table_name}.{column_name}")
    return hidden


def _hidden_reference(entry, table_name: str, hidden) -> bool:
    """True when a discovery entry describes a column this role may not read."""
    if not isinstance(entry, dict):
        return False
    column = entry.get("column")
    if column and f"{table_name}.{column}" in hidden:
        return True
    referred_table = entry.get("references_table")
    referred_column = entry.get("references_column")
    if referred_table and referred_column and f"{referred_table}.{referred_column}" in hidden:
        return True
    return False


def restrict_schema(schema: dict, policy: dict) -> tuple[dict, list[str]]:
    """The schema a role is allowed to see, plus what was removed.

    Removing the column here is what stops the generator from ever naming it;
    the predicate compiled by `compile_predicate` is what stops the engine from
    reading rows the role has no claim on.
    """
    if policy.get("unrestricted"):
        return dict(schema), []
    hidden = masked_columns(schema, policy)
    filtered = policy.get("row_filters") or {}
    kept_tables, removed = [], []
    for table in schema.get("tables", []):
        table_name = str(table.get("name", ""))
        kept, dropped = [], []
        for column in table.get("columns", []):
            if f"{table_name}.{column.get('name')}" in hidden:
                dropped.append(str(column.get("name")))
            else:
                kept.append(column)
        if dropped:
            removed.extend(f"{table_name}.{name}" for name in dropped)
        entry = {**table, "columns": kept,
                 "foreign_keys": [fk for fk in table.get("foreign_keys", [])
                                  if not _hidden_reference(fk, table_name, hidden)],
                 "observations": [o for o in table.get("observations", [])
                                  if not _hidden_reference(o, table_name, hidden)]}
        if table_name in filtered:
            # The real table size is not this role's to know. The gateway replaces
            # this with the size of the filtered view it *is* entitled to read.
            entry["row_count"] = None
            entry["row_count_withheld"] = True
            # "Discovered table with 793 rows." repeats that number in prose.
            entry["observations"] = [o for o in entry["observations"]
                                     if o.get("column") is not None]
        kept_tables.append(entry)
    restricted = {**schema, "tables": kept_tables}
    warnings = list(schema.get("warnings", []))
    if removed:
        # The DDL summary lists every column, hidden ones included, and free text
        # cannot be censored reliably — so a role with anything hidden gets none of it.
        restricted["summary_text"] = ""
        restricted["observations"] = [
            o for o in schema.get("observations", []) if not _hidden_reference(o, "", hidden)]
        warnings.append("Schema summary withheld: it describes columns hidden from "
                        "your access level.")
        warnings.append(f"{len(removed)} column(s) are hidden from your access level: "
                        f"{', '.join(sorted(removed))}.")
    restricted["warnings"] = warnings
    return restricted, removed


# ------------------------------------------------------------------ role language

_ROLE_PHRASE = re.compile(r"\b(?:the\s+)?role\s+'?(?:admin|analyst|viewer)'?\b",
                          re.IGNORECASE)
_ROLE_WORD = re.compile(r"\b(?:admin|analyst|viewer)\b", re.IGNORECASE)


def neutralize_role_names(text):
    """Rewrite access-role vocabulary in model-written prose.

    The model is shown schema warnings about withheld columns; without this it can
    echo `viewer` or `hidden from role` into a clarification question meant for a
    business user. Names of the roles are internal policy vocabulary — the reader
    needs the effect ("restricted", "your access level"), not the label.
    """
    if not isinstance(text, str) or not text:
        return text
    text = _ROLE_PHRASE.sub("your access level", text)
    return _ROLE_WORD.sub("restricted", text)


# --------------------------------------------------------------------- predicates

def _identifier(name, where: str) -> str:
    text = str(name or "")
    if not _IDENTIFIER.match(text):
        raise PolicyError(f"unsafe identifier in {where}: {text!r}")
    return text


def _column_ref(column: str, table: str, where: str) -> exp.Column:
    parts = str(column).split(".")
    if len(parts) == 2:
        table = _identifier(parts[0], f"{where}.column")
        column = parts[1]
    return exp.Column(this=exp.to_identifier(_identifier(column, f"{where}.column")),
                      table=exp.to_identifier(_identifier(table, f"{where}.table")))


def _validate_against_schema(table: str, column: str, schema: dict | None, where: str) -> None:
    """A policy that names something the database does not have is a broken policy,
    not a harmless one: it would silently grant rows the author meant to exclude."""
    if schema is None:
        return
    names = {str(t.get("name", "")): {str(c.get("name", "")) for c in t.get("columns", [])}
             for t in schema.get("tables", [])}
    if table not in names:
        raise PolicyError(f"{where}: table {table!r} is not in the schema")
    if column not in names[table]:
        raise PolicyError(f"{where}: column {table}.{column} is not in the schema")


def compile_predicate(spec: dict, table: str, schema: dict | None = None,
                      dialect: str = "sqlite", depth: int = 0) -> str:
    """Compile one structured row filter into a SQL boolean expression.

    Raises PolicyError on an unknown key, an unsafe identifier, a value type that
    has no SQL literal form, or a policy that names a column the schema lacks.
    """
    if depth > MAX_PREDICATE_DEPTH:
        raise PolicyError("row filter nests too deeply")
    if not isinstance(spec, dict) or not spec:
        raise PolicyError(f"row filter for {table} must be a non-empty object")
    unknown = set(spec) - _PREDICATE_KEYS
    if unknown:
        raise PolicyError(f"unknown row-filter key(s) for {table}: {', '.join(sorted(unknown))}")

    if "all_of" in spec or "any_of" in spec:
        key = "all_of" if "all_of" in spec else "any_of"
        parts = [compile_predicate(item, table, schema, dialect, depth + 1)
                 for item in spec[key]]
        if not parts:
            raise PolicyError(f"{key} for {table} is empty")
        joined = " AND ".join(f"({part})" for part in parts) if key == "all_of" \
            else " OR ".join(f"({part})" for part in parts)
        return f"({joined})"

    if "semi_join" in spec:
        inner = spec["semi_join"] or {}
        column = _identifier(inner.get("column"), f"{table}.semi_join.column")
        from_table = _identifier(inner.get("from_table"), f"{table}.semi_join.from_table")
        where = inner.get("where")
        if not isinstance(where, dict):
            raise PolicyError(f"{table}.semi_join needs a where object")
        _validate_against_schema(table, column, schema, f"{table}.semi_join")
        expression = compile_predicate(where, from_table, schema, dialect, depth + 1)
        subquery = (exp.select(_column_ref(column=column, table=from_table, where=table),
                              ).from_(exp.to_table(from_table), copy=False)
                    .where(exp.condition(expression), copy=False))
        node = exp.In(this=_column_ref(column, table, f"{table}.semi_join"),
                      query=exp.Subquery(this=subquery))
        return node.sql(dialect=dialect)

    column = spec.get("column")
    if not column:
        raise PolicyError(f"row filter for {table} needs a column, semi_join, all_of or any_of")
    where = f"{table}.{column}"
    _validate_against_schema(table, str(column), schema, where)
    ref = _column_ref(str(column), spec.get("table") or table, where)

    if "in" in spec:
        values = spec["in"]
        if not isinstance(values, list) or not values:
            raise PolicyError(f"{where}.in must be a non-empty list")
        converted = []
        for value in values:
            if not isinstance(value, (str, int, float, bool)) or isinstance(value, float) and value != value:
                raise PolicyError(f"{where}.in has an unusable value: {value!r}")
            converted.append(exp.convert(value))
        return exp.In(this=ref, expressions=converted).sql(dialect=dialect)

    if "like" in spec:
        if not isinstance(spec["like"], str):
            raise PolicyError(f"{where}.like must be a string")
        return exp.Like(this=ref, expression=exp.convert(spec["like"])).sql(dialect=dialect)

    emitted = []
    for op, node_type in _COMPARISONS.items():
        if op in spec:
            value = spec[op]
            if not isinstance(value, (str, int, float, bool)):
                raise PolicyError(f"{where}.{op} has an unusable value: {value!r}")
            emitted.append(node_type(this=ref, expression=exp.convert(value)).sql(dialect=dialect))
    if not emitted:
        raise PolicyError(f"row filter for {where} has no operator")
    return " AND ".join(f"({part})" for part in emitted)


def row_predicates(policy: dict, schema: dict | None = None,
                   dialect: str = "sqlite") -> dict[str, str]:
    """Compiled `table -> boolean expression` for every filtered table."""
    if policy.get("unrestricted"):
        return {}
    return {table: compile_predicate(spec, table, schema, dialect)
            for table, spec in sorted((policy.get("row_filters") or {}).items())}


def restrict_query(sql: str, policy: dict, schema: dict, dialect: str = "sqlite") -> dict:
    """Rewrite a validated query so the engine can only see rows and columns the role owns.

    Each protected table reference becomes an inline view —
    `(SELECT <permitted columns> FROM <table> WHERE <row filter>) AS <table>` —
    which is enforced by SQLite itself rather than by a check on the model's output.
    A query that names a hidden column then fails on the engine instead of leaking it.
    """
    if policy.get("unrestricted"):
        return {"sql": sql, "changed": False, "tables": [], "predicates": {},
                "masked_columns": []}
    predicates = row_predicates(policy, schema, dialect)
    hidden = masked_columns(schema, policy)
    columns = {str(table.get("name", "")): [str(c.get("name", ""))
                                            for c in table.get("columns", [])]
               for table in schema.get("tables", [])}
    protected = {table for table, names in columns.items()
                 if table in predicates
                 or any(f"{table}.{name}" in hidden for name in names)}
    if not protected:
        return {"sql": sql, "changed": False, "tables": [], "predicates": {},
                "masked_columns": []}

    from sqlglot import parse_one
    from sqlglot.optimizer.scope import traverse_scope

    try:
        tree = parse_one(sql, dialect=dialect)
    except Exception as exc:
        raise PolicyError(f"query could not be parsed for enforcement: {exc}") from exc
    if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery, exp.Paren)):
        raise PolicyError("only a read-only query can be restricted")

    # Scope resolution decides what each reference means. A name that resolves to
    # a CTE is the model's own alias and stays untouched; a real protected table
    # is wrapped wherever it appears — including inside a CTE body, so
    # `WITH orders AS (SELECT ... FROM orders ...)` can no longer read the
    # unfiltered table just by shadowing its name.
    try:
        scopes = traverse_scope(tree)
    except Exception as exc:
        raise PolicyError(f"query scopes could not be resolved for enforcement: {exc}") from exc

    used, seen = set(), set()
    for scope in scopes:
        for source in scope.sources.values():
            name = str(source.name) if isinstance(source, exp.Table) else ""
            if name not in protected or id(source) in seen:
                continue
            seen.add(id(source))
            permitted = [column for column in columns[name]
                         if f"{name}.{column}" not in hidden]
            if not permitted:
                raise PolicyError(f"role '{policy['role']}' may not read table {name}")
            inner = exp.select(*[exp.column(column, quoted=True) for column in permitted])
            inner = inner.from_(exp.to_table(name), copy=False)
            if name in predicates:
                inner = inner.where(exp.condition(predicates[name]), copy=False)
            alias = source.alias_or_name or name
            source.replace(exp.Alias(this=exp.Subquery(this=inner), alias=exp.to_identifier(alias)))
            used.add(name)

    return {"sql": tree.sql(dialect=dialect), "changed": bool(used),
            "tables": sorted(used), "predicates": {t: predicates[t] for t in sorted(used)
                                                   if t in predicates},
            "masked_columns": sorted(column for column in hidden
                                     if column.split(".")[0] in used)}


def restrict_profile(profile: dict, policy: dict, hidden: set[str],
                     predicates: dict) -> dict:
    """Strip hidden columns and whole-table counts a row-limited role must not infer.

    A profile is aggregate data, and aggregates leak: reporting that `customers`
    holds 793 rows to a role that may only see the East region hands over a count
    it is not entitled to, so the number is withheld rather than softened.
    """
    if policy.get("unrestricted"):
        return dict(profile)
    tables = []
    for table in profile.get("tables", []):
        name = str(table.get("name", ""))
        # A list, not a set: two columns with the same keep/drop verdict would collapse
        # into one element and misalign the zip, hiding columns the role may read.
        kept = [f"{name}.{column.get('name')}" not in hidden
                for column in table.get("columns", [])]
        entry = {**table,
                 "columns": [column for column, keep in
                             zip(table.get("columns", []), kept) if keep],
                 "observations": [o for o in table.get("observations", [])
                                  if f"{name}.{o.get('column')}" not in hidden]}
        entry["column_count"] = len(entry["columns"])
        if name in predicates:
            entry["row_count"] = None
            entry["duplicate_row_count"] = None
            entry["duplicate_group_count"] = None
            # Every profile number is computed over the whole table: "Profiled 793
            # rows; 4 distinct values" tells a role that may only read 220 rows
            # both the real size and how many regions it cannot see. The numbers
            # and the prose that repeats them are withheld instead of softened.
            entry["observations"] = []
            entry["columns"] = [{key: column.get(key) for key in
                                 ("name", "type", "nullable", "pk") if key in column}
                                for column in entry["columns"]]
            entry["warnings"] = list(table.get("warnings", [])) + [
                f"Column statistics withheld: role '{policy['role']}' may only read a "
                f"filtered subset of {name}."]
        tables.append(entry)
    summary = str(profile.get("summary_text") or "")
    warnings = list(profile.get("warnings", []))
    if summary and hidden:
        # The summary is free text describing every column; it cannot be redacted
        # reliably, so a role with anything hidden simply does not get it.
        summary = ""
        warnings.append("Database summary withheld: it describes columns hidden from "
                        "your access level.")
    if hidden:
        warnings.append(f"{len(hidden)} column(s) are hidden from your access level.")
    return {**profile, "tables": tables, "summary_text": summary, "warnings": warnings}


def policy_note(policy: dict, predicates: dict, removed: list[str]) -> str:
    """A human-readable summary of what was withheld, for the answer's warnings."""
    if policy.get("unrestricted"):
        return ""
    parts = []
    if predicates:
        parts.append("row limit: " + "; ".join(f"{table} → {expression}"
                                               for table, expression in predicates.items()))
    if removed:
        parts.append(f"{len(removed)} column(s) hidden")
    if policy.get("max_rows"):
        parts.append(f"at most {policy['max_rows']} rows")
    return f"Role '{policy['role']}' policy on {policy['dataset']}: " + ", ".join(parts) \
        if parts else ""
