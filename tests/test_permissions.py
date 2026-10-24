"""Role, row-limit and column-mask policy tests (Phase 2 part 1)."""
import json
from pathlib import Path

import pytest

from gateway import permissions
from gateway.permissions import PolicyError, compile_predicate, masked_columns, policy_for, \
    restrict_schema, resolve_principal, row_predicates, load_policies, policy_note

SCHEMA = {
    "dialect": "sqlite",
    "tables": [
        {"name": "customers", "row_count": 793, "columns": [
            {"name": "customer_id", "type": "TEXT"}, {"name": "name", "type": "TEXT"},
            {"name": "region", "type": "TEXT"}, {"name": "email", "type": "TEXT"},
            {"name": "postal_code", "type": "INTEGER"}]},
        {"name": "orders", "row_count": 4922, "columns": [
            {"name": "order_id", "type": "TEXT"}, {"name": "customer_id", "type": "TEXT"},
            {"name": "order_date", "type": "TEXT"}, {"name": "status", "type": "TEXT"}]},
        {"name": "line_items", "row_count": 9800, "columns": [
            {"name": "order_id", "type": "TEXT"}, {"name": "sales", "type": "REAL"}]},
    ],
    "warnings": [],
}

POLICIES = {
    "role_defaults": {
        "admin": {"unrestricted": True},
        "analyst": {"mask_name_patterns": ["email"], "max_rows": 1000},
        "viewer": {"mask_name_patterns": ["email", "postal_code"], "max_rows": 200},
    },
    "users": {"root": {"role": "admin"}, "rita": {"role": "analyst", "label": "Analyst"},
              "ghost": {"role": "superuser"}},
    "datasets": {"superstore": {"viewer": {
        "row_filters": {"customers": {"column": "region", "in": ["East"]},
                        "orders": {"semi_join": {"column": "customer_id",
                                                 "from_table": "customers",
                                                 "where": {"column": "region", "in": ["East"]}}}},
        "masked_columns": ["customers.name"]}}},
}


# ---------------------------------------------------------------- identities
def test_unknown_identity_is_a_viewer_never_an_admin():
    assert resolve_principal("", POLICIES)["role"] == "viewer"
    assert resolve_principal("nobody", POLICIES)["role"] == "viewer"
    assert resolve_principal(None, POLICIES)["role"] == "viewer"


def test_known_roles_resolve_and_a_bogus_role_falls_back():
    assert resolve_principal("root", POLICIES)["role"] == "admin"
    assert resolve_principal("rita", POLICIES)["role"] == "analyst"
    assert resolve_principal("ghost", POLICIES)["role"] == "viewer"


def test_missing_policy_file_still_restricts():
    empty = load_policies(Path(__file__).parent / "does-not-exist.json")
    assert resolve_principal("root", empty)["role"] == "viewer"
    assert policy_for(empty, "superstore", "viewer")["max_rows"] is None


# ---------------------------------------------------------------- policies
def test_admin_is_unrestricted_and_viewer_gets_the_dataset_rules():
    assert policy_for(POLICIES, "superstore", "admin")["unrestricted"] is True
    viewer = policy_for(POLICIES, "superstore", "viewer")
    assert set(viewer["row_filters"]) == {"customers", "orders"}
    assert viewer["masked_columns"] == ["customers.name"]
    assert viewer["max_rows"] == 200


def test_role_defaults_and_dataset_entries_merge():
    analyst = policy_for(POLICIES, "superstore", "analyst")
    assert analyst["row_filters"] == {}
    assert analyst["mask_name_patterns"] == ["email"]
    assert analyst["max_rows"] == 1000


def test_an_unknown_role_is_rejected():
    with pytest.raises(PolicyError):
        policy_for(POLICIES, "superstore", "owner")


# ------------------------------------------------------------------- masking
def test_pattern_and_explicit_masks_both_apply():
    viewer = policy_for(POLICIES, "superstore", "viewer")
    assert masked_columns(SCHEMA, viewer) == {"customers.email", "customers.postal_code",
                                              "customers.name"}


def test_restricted_schema_hides_columns_and_says_so():
    viewer = policy_for(POLICIES, "superstore", "viewer")
    restricted, removed = restrict_schema(SCHEMA, viewer)
    names = {c["name"] for t in restricted["tables"] if t["name"] == "customers"
             for c in t["columns"]}
    assert names == {"customer_id", "region"}
    assert len(removed) == 3 and "customers.email" in removed
    assert any("hidden" in warning for warning in restricted["warnings"])
    assert SCHEMA["tables"][0]["columns"] and "email" in [
        c["name"] for c in SCHEMA["tables"][0]["columns"]], "input schema must not mutate"


def test_admin_schema_is_untouched():
    admin = policy_for(POLICIES, "superstore", "admin")
    restricted, removed = restrict_schema(SCHEMA, admin)
    assert removed == [] and masked_columns(SCHEMA, admin) == set()
    assert restricted["tables"] == SCHEMA["tables"]


def test_schema_warnings_never_name_the_role():
    # These warnings go into the model's prompt; a role label there comes back
    # echoed in clarification text meant for a business user.
    viewer = policy_for(POLICIES, "superstore", "viewer")
    restricted, removed = restrict_schema(SCHEMA, viewer)
    assert removed
    assert not any(role in warning.lower() for warning in restricted["warnings"]
                   for role in ("viewer", "analyst", "admin"))


def test_neutralize_role_names_rewrites_policy_vocabulary():
    out = permissions.neutralize_role_names(
        "email is hidden from role 'viewer'; ask an admin or the analyst.")
    assert not any(role in out.lower() for role in ("viewer", "analyst", "admin"))
    assert "your access level" in out and "restricted" in out
    assert permissions.neutralize_role_names("") == ""
    assert permissions.neutralize_role_names(None) is None


def test_a_bad_mask_pattern_fails_closed():
    with pytest.raises(PolicyError):
        masked_columns(SCHEMA, {"mask_name_patterns": ["email("]})


# --------------------------------------------------------------- predicates
def test_in_and_comparison_predicates_compile_to_sql():
    assert compile_predicate({"column": "region", "in": ["East"]}, "customers", SCHEMA) \
        == 'customers.region IN (\'East\')'
    compiled = compile_predicate({"column": "order_date", "gte": "2023-01-01",
                                  "lt": "2024-01-01"}, "orders", SCHEMA)
    assert "orders.order_date >= '2023-01-01'" in compiled
    assert "orders.order_date < '2024-01-01'" in compiled


def test_semi_join_becomes_a_bound_subquery_on_the_owning_table():
    spec = POLICIES["datasets"]["superstore"]["viewer"]["row_filters"]["orders"]
    compiled = compile_predicate(spec, "orders", SCHEMA)
    assert compiled.startswith("orders.customer_id IN (")
    assert "FROM customers" in compiled and "customers.region IN ('East')" in compiled


def test_nested_logic_combinators():
    spec = {"all_of": [{"column": "region", "in": ["East"]},
                       {"any_of": [{"column": "customer_id", "like": "A%"},
                                   {"column": "name", "eq": "Ann"}]}]}
    compiled = compile_predicate(spec, "customers", SCHEMA)
    assert " AND " in compiled and " OR " in compiled


def test_a_value_cannot_escape_its_literal():
    from sqlglot import parse_one

    hostile = {"column": "region", "in": ["East'); DROP TABLE customers;--"]}
    compiled = compile_predicate(hostile, "customers", SCHEMA)
    statement = parse_one(f"SELECT 1 FROM customers WHERE {compiled}", dialect="sqlite")
    assert isinstance(statement, parse_one("SELECT 1").__class__), \
        "the predicate must stay one expression inside one SELECT"
    assert "DROP TABLE" in compiled, "kept as data inside the literal, not as syntax"


@pytest.mark.parametrize("table,spec,reason", [
    ("customers", {"column": "region; DROP TABLE x", "in": ["East"]}, "unsafe identifier"),
    ("customers", {"column": "nope", "in": ["East"]}, "column not in schema"),
    ("customers", {"column": "region", "in": []}, "empty in"),
    ("customers", {"column": "region", "in": [{"bad": 1}]}, "unusable value"),
    ("customers", {"column": "region", "eq": None}, "null comparison"),
    ("customers", {"column": "region"}, "no operator"),
    ("customers", {"between": [1, 2]}, "unknown key"),
    ("customers", {}, "empty spec"),
    ("orders", {"semi_join": {"column": "customer_id", "from_table": "customers"}},
     "missing where"),
])
def test_unsafe_or_broken_predicates_are_refused(table, spec, reason):
    with pytest.raises(PolicyError):
        compile_predicate(spec, table, SCHEMA)


def test_a_predicate_naming_an_unknown_table_is_refused():
    with pytest.raises(PolicyError):
        compile_predicate({"column": "region", "in": ["East"]}, "accounts", SCHEMA)


def test_depth_limit_stops_a_runaway_policy():
    spec = {"column": "region", "in": ["East"]}
    for _ in range(6):
        spec = {"semi_join": {"column": "customer_id", "from_table": "customers",
                             "where": spec}}
    with pytest.raises(PolicyError):
        compile_predicate(spec, "orders", SCHEMA)


def test_row_predicates_cover_every_filtered_table():
    viewer = policy_for(POLICIES, "superstore", "viewer")
    predicates = row_predicates(viewer, SCHEMA)
    assert set(predicates) == {"customers", "orders"}
    assert row_predicates(policy_for(POLICIES, "superstore", "admin"), SCHEMA) == {}


def test_policy_note_reports_what_was_withheld():
    viewer = policy_for(POLICIES, "superstore", "viewer")
    note = policy_note(viewer, row_predicates(viewer, SCHEMA), ["customers.name"])
    assert "row limit" in note and "column(s) hidden" in note and "200 rows" in note
    admin = policy_for(POLICIES, "superstore", "admin")
    assert policy_note(admin, {}, []) == ""


# ------------------------------------------------------------- enforcement
def test_admin_query_passes_through_unchanged():
    from gateway.permissions import restrict_query

    admin = policy_for(POLICIES, "superstore", "admin")
    out = restrict_query("SELECT region FROM customers", admin, SCHEMA)
    assert out == {"sql": "SELECT region FROM customers", "changed": False, "tables": [],
                   "predicates": {}, "masked_columns": []}


def test_each_protected_table_becomes_an_inline_view():
    from gateway.permissions import restrict_query

    viewer = policy_for(POLICIES, "superstore", "viewer")
    out = restrict_query("SELECT orders.order_id FROM orders "
                         "JOIN customers ON customers.customer_id = orders.customer_id",
                         viewer, SCHEMA)
    assert out["changed"] and set(out["tables"]) == {"customers", "orders"}
    assert "FROM (SELECT" in out["sql"]
    assert "customers.region IN ('East')" in out["sql"]
    assert out["masked_columns"] == ["customers.email", "customers.name",
                                     "customers.postal_code"]


def test_a_table_alias_survives_the_rewrite():
    from gateway.permissions import restrict_query

    viewer = policy_for(POLICIES, "superstore", "viewer")
    out = restrict_query("SELECT c.region FROM customers AS c", viewer, SCHEMA)
    assert "AS c" in out["sql"] and "FROM customers" in out["sql"]
    from sqlglot import parse_one
    assert parse_one(out["sql"], dialect="sqlite") is not None


def test_select_star_cannot_reach_a_masked_column():
    from gateway.permissions import restrict_query

    viewer = policy_for(POLICIES, "superstore", "viewer")
    out = restrict_query("SELECT * FROM customers", viewer, SCHEMA)
    assert '"customer_id"' in out["sql"] and '"region"' in out["sql"]
    for hidden in ('"email"', '"postal_code"', '"name"'):
        assert hidden not in out["sql"], f"{hidden} must not be selectable"


def test_a_cte_named_like_a_real_table_is_not_wrapped():
    from gateway.permissions import restrict_query

    viewer = policy_for(POLICIES, "superstore", "viewer")
    sql = "WITH orders AS (SELECT 'X' AS order_id) SELECT order_id FROM orders"
    out = restrict_query(sql, viewer, SCHEMA)
    assert out["tables"] == [], "the model's own CTE is not the stored table"


def test_a_cte_body_that_reads_a_protected_table_is_wrapped():
    from gateway.permissions import restrict_query

    viewer = policy_for(POLICIES, "superstore", "viewer")
    out = restrict_query("WITH recent AS (SELECT order_id, total FROM orders "
                         "WHERE total > 10) SELECT * FROM recent", viewer, SCHEMA)
    assert out["tables"] == ["orders"]
    assert "region IN ('East')" in out["sql"], "the CTE body must read only East rows"


def test_a_shadowing_cte_still_wraps_its_own_real_table_read():
    from gateway.permissions import restrict_query

    viewer = policy_for(POLICIES, "superstore", "viewer")
    out = restrict_query("WITH orders AS (SELECT order_id FROM orders) "
                         "SELECT order_id FROM orders", viewer, SCHEMA)
    assert out["tables"] == ["orders"]
    sql = out["sql"]
    assert sql.count("FROM (SELECT") == 1, "only the real read inside the CTE is wrapped"
    assert "region IN ('East')" in sql or "customer_id IN" in sql
    assert sql.rstrip().endswith("FROM orders"), "the outer name still resolves to the CTE"


def test_a_scalar_subquery_reading_a_protected_table_is_wrapped():
    from gateway.permissions import restrict_query

    viewer = policy_for(POLICIES, "superstore", "viewer")
    out = restrict_query("SELECT (SELECT COUNT(*) FROM customers) AS c, "
                         "line_items.order_id FROM line_items", viewer, SCHEMA)
    assert out["tables"] == ["customers"]
    assert "region IN ('East')" in out["sql"]


def test_an_unprotected_query_is_left_alone():
    from gateway.permissions import restrict_query

    viewer = policy_for(POLICIES, "superstore", "viewer")
    out = restrict_query("SELECT sales FROM line_items", viewer, SCHEMA)
    assert out["changed"] is False


def test_a_policy_that_hides_every_column_of_a_used_table_refuses():
    from gateway.permissions import restrict_query

    strict = {"role": "viewer", "row_filters": {}, "masked_columns": [
        "customers.customer_id", "customers.name", "customers.region", "customers.email",
        "customers.postal_code"], "mask_name_patterns": [], "unrestricted": False}
    with pytest.raises(PolicyError):
        restrict_query("SELECT region FROM customers", strict, SCHEMA)


PROFILE = {
    "status": "success",
    "tables": [
        {"name": "customers", "row_count": 793, "column_count": 5,
         "duplicate_row_count": 12, "duplicate_group_count": 4,
         "columns": [{"name": "customer_id", "type": "TEXT", "distinct_count": 793,
                      "null_percentage": 0.0},
                     {"name": "name", "type": "TEXT", "distinct_count": 793},
                     {"name": "region", "type": "TEXT", "distinct_count": 4},
                     {"name": "email", "type": "TEXT"},
                     {"name": "postal_code", "type": "INTEGER"}],
         "observations": [{"code": "table_profiled", "message": "Profiled 793 rows.",
                           "table": "customers", "column": None}]},
        {"name": "line_items", "row_count": 9800, "column_count": 2,
         "columns": [{"name": "order_id", "type": "TEXT"}, {"name": "sales", "type": "REAL"}],
         "observations": []},
    ],
    "observations": [{"code": "database_profiled", "message": "Profiled 2 tables.",
                      "table": None, "column": None}],
    "summary_text": "TABLE customers (customer_id TEXT, name TEXT, region TEXT, "
                    "email TEXT, postal_code INTEGER)",
    "warnings": [],
}


def _viewer_policy():
    return policy_for(POLICIES, "superstore", "viewer")


def test_profile_keeps_the_columns_the_role_may_read_in_order():
    """The keep/drop verdicts must stay aligned with the columns: a set of booleans
    collapses duplicates and silently hides columns the role is entitled to."""
    from gateway.permissions import restrict_profile

    hidden = masked_columns(SCHEMA, _viewer_policy())
    out = restrict_profile(PROFILE, _viewer_policy(), hidden,
                           row_predicates(_viewer_policy(), SCHEMA))
    customers = out["tables"][0]
    assert [column["name"] for column in customers["columns"]] == ["customer_id", "region"]
    assert customers["column_count"] == 2


def test_profile_withholds_every_whole_table_number_from_a_filtered_role():
    from gateway.permissions import restrict_profile

    hidden = masked_columns(SCHEMA, _viewer_policy())
    out = restrict_profile(PROFILE, _viewer_policy(), hidden,
                           row_predicates(_viewer_policy(), SCHEMA))
    customers = out["tables"][0]
    for key in ("row_count", "duplicate_row_count", "duplicate_group_count"):
        assert customers[key] is None
    # "Profiled 793 rows" and "4 distinct regions" are whole-table aggregates too
    assert customers["observations"] == []
    assert all("distinct" not in column and "null_percentage" not in column
               for column in customers["columns"])
    assert "email" not in json.dumps(out) and "793 rows" not in json.dumps(out)


def test_profile_withholds_a_summary_that_describes_hidden_columns():
    from gateway.permissions import restrict_profile

    hidden = masked_columns(SCHEMA, _viewer_policy())
    out = restrict_profile(PROFILE, _viewer_policy(), hidden,
                           row_predicates(_viewer_policy(), SCHEMA))
    assert out["summary_text"] == ""
    assert any("summary withheld" in warning.casefold() or "hidden" in warning
               for warning in out["warnings"])


def test_profile_leaves_an_unrestricted_role_alone():
    from gateway.permissions import restrict_profile

    admin = policy_for(POLICIES, "superstore", "admin")
    assert restrict_profile(PROFILE, admin, set(), {}) == PROFILE


def test_filtered_schema_replaces_the_real_row_count_with_a_withheld_one():
    _visible, removed = restrict_schema(SCHEMA, _viewer_policy())
    tables = {table["name"]: table for table in _visible["tables"]}
    assert tables["customers"]["row_count"] is None
    assert tables["customers"]["row_count_withheld"] is True
    # line_items has no row filter of its own, so its size stays as reported
    assert tables["line_items"]["row_count"] == 9800
    assert set(removed) == {"customers.name", "customers.email", "customers.postal_code"}


# ------------------------------------------------- the shipped policy file
def test_shipped_policies_only_name_columns_that_exist():
    """A typo in policies.json would silently widen access, so the real file is
    checked against the real schema shape before anything else runs."""
    policies = json.loads(Path(permissions.POLICIES_FILE).read_text(encoding="utf-8"))
    fixture = {
        "superstore": SCHEMA,
        "sample_retail": {"tables": [
            {"name": "orders", "columns": [{"name": c} for c in
                                           ("id", "customer_id", "order_date", "status",
                                            "total_amount")]},
            {"name": "order_items", "columns": [{"name": c} for c in
                                                ("id", "order_id", "product_id", "quantity",
                                                 "unit_price")]}]},
    }
    for dataset, roles in policies["datasets"].items():
        schema = fixture[dataset]
        known = {t["name"]: {c["name"] for c in t["columns"]} for t in schema["tables"]}
        for role, entry in roles.items():
            policy = policy_for(policies, dataset, role)
            row_predicates(policy, schema)
            for name in entry.get("masked_columns", []):
                table, _, column = name.partition(".")
                assert column in known.get(table, set()), f"{dataset}/{role} masks {name}"
