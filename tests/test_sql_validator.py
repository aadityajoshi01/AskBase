"""Tests for backend.sql_validator — Agent 3 read-only SQL safety boundary.

Runs without a database, a live model or a GPU: validation is pure parsing.
Covers accepted read-only forms, rejected writes/DDL/transaction control,
multi-statement and smuggled-write payloads, denied functions, unsupported
dialects and malformed input (report §5 testing requirements).
"""
import pytest

from backend.sql_validator import (
    MAX_MESSAGE_LENGTH,
    MAX_SQL_LENGTH,
    SUPPORTED_DIALECTS,
    validate_sql,
)

RESULT_KEYS = {
    "status", "valid", "normalized_sql", "dialect", "requested_dialect",
    "statement_kind", "tables", "error", "errors", "checks_passed",
}


def codes(result):
    return result["error"]["type"] if result["error"] else None


# --- accepted read-only forms -------------------------------------------------


def test_accepts_simple_select():
    result = validate_sql("SELECT 1")
    assert result["status"] == "ok" and result["valid"] is True
    assert result["normalized_sql"] == "SELECT 1"
    assert result["statement_kind"] == "select"
    assert result["error"] is None and result["errors"] == []


def test_accepts_projection_with_filter():
    result = validate_sql("SELECT a, b FROM t WHERE b > 10")
    assert result["status"] == "ok"
    assert result["tables"] == ["t"]


def test_accepts_join():
    assert validate_sql(
        "SELECT t.a, u.b FROM t LEFT JOIN u ON t.id = u.id WHERE t.a IS NOT NULL"
    )["status"] == "ok"


def test_accepts_aggregation_group_and_having():
    result = validate_sql("SELECT region, COUNT(*) AS n FROM sales GROUP BY region HAVING COUNT(*) > 1")
    assert result["status"] == "ok"
    assert result["tables"] == ["sales"]


def test_accepts_cte():
    result = validate_sql("WITH x AS (SELECT 1 AS n) SELECT n FROM x")
    assert result["status"] == "ok" and result["statement_kind"] == "select"


def test_accepts_subquery_in_from():
    assert validate_sql("SELECT s.a FROM (SELECT a FROM t) AS s")["status"] == "ok"


def test_accepts_in_subquery():
    assert validate_sql("SELECT a FROM t WHERE a IN (SELECT b FROM u)")["status"] == "ok"


def test_accepts_parenthesised_subquery():
    assert validate_sql("(SELECT 1)")["statement_kind"] == "subquery"


@pytest.mark.parametrize("sql", [
    "SELECT 1 UNION SELECT 2",
    "SELECT 1 UNION ALL SELECT 2",
    "SELECT 1 EXCEPT SELECT 2",
    "SELECT 1 INTERSECT SELECT 2",
])
def test_accepts_set_operations(sql):
    assert validate_sql(sql)["statement_kind"] == "set_operation"


def test_accepts_order_limit_offset_distinct_case():
    assert validate_sql(
        "SELECT DISTINCT CASE WHEN a > 1 THEN 'x' ELSE 'y' END AS c FROM t ORDER BY a DESC LIMIT 10 OFFSET 5"
    )["status"] == "ok"


def test_accepts_common_functions():
    for sql in ["SELECT MAX(a) FROM t", "SELECT ROUND(AVG(x), 2) FROM t",
                "SELECT GROUP_CONCAT(b) FROM t", "SELECT DATE('now') AS d",
                "SELECT sqlite_version()", "SELECT json_extract(x, '$.a') FROM t"]:
        assert validate_sql(sql)["status"] == "ok", sql


def test_semicolon_inside_string_literal_is_not_a_second_statement():
    result = validate_sql("SELECT a FROM t WHERE b = 'a;b'")
    assert result["status"] == "ok"
    assert "'a;b'" in result["normalized_sql"]


def test_trailing_semicolon_is_one_statement():
    assert validate_sql("SELECT 1;")["status"] == "ok"


def test_comment_after_terminator_is_not_a_second_statement():
    assert validate_sql("SELECT 1; -- trailing note")["status"] == "ok"


def test_inline_comment_is_allowed_and_dropped_from_normalized_sql():
    result = validate_sql("SELECT/*hint*/ 1 -- note")
    assert result["status"] == "ok"
    assert result["normalized_sql"] == "SELECT 1"


def test_commented_text_cannot_hide_a_second_statement():
    assert codes(validate_sql("SELECT 1 -- ok\n; DROP TABLE t")) == "multiple_statements"


def test_keywords_are_case_insensitive():
    assert validate_sql("SeLeCt 1 FrOm t")["status"] == "ok"


# --- rejected statement classes ----------------------------------------------


@pytest.mark.parametrize("sql", [
    "INSERT INTO t VALUES (1)",
    "INSERT INTO t (a) SELECT 1",
    "UPDATE t SET a = 1",
    "DELETE FROM t",
])
def test_rejects_writes(sql):
    assert codes(validate_sql(sql)) == "unsupported_statement"


@pytest.mark.parametrize("sql", [
    "CREATE TABLE t (a INT)",
    "CREATE INDEX i ON t (a)",
    "DROP TABLE t",
    "ALTER TABLE t ADD COLUMN a INT",
    "TRUNCATE TABLE t",
])
def test_rejects_ddl(sql):
    assert codes(validate_sql(sql)) == "unsupported_statement"


@pytest.mark.parametrize("sql", ["BEGIN", "COMMIT", "ROLLBACK", "START TRANSACTION"])
def test_rejects_transaction_control(sql):
    assert codes(validate_sql(sql)) == "unsupported_statement"


@pytest.mark.parametrize("sql", [
    "GRANT SELECT ON t TO u",
    "REVOKE SELECT ON t FROM u",
    "SET a = 1",
    "USE mydb",
])
def test_rejects_session_and_privilege_statements(sql):
    assert codes(validate_sql(sql)) == "unsupported_statement"


@pytest.mark.parametrize("sql", [
    "VACUUM",
    "PRAGMA table_info(t)",
    "ATTACH DATABASE 'file.db' AS other",
    "DETACH other",
    "CALL sp_executesql('x')",
])
def test_rejects_dialect_specific_maintenance_statements(sql):
    assert codes(validate_sql(sql)) == "unsupported_statement"


def test_error_message_names_the_statement_type():
    error = validate_sql("DELETE FROM t")["error"]
    assert "Delete" in error["message"] or "read-only" in error["message"]


# --- rejected smuggling routes -----------------------------------------------


def test_rejects_select_for_update_lock():
    assert codes(validate_sql("SELECT * FROM t FOR UPDATE")) == "forbidden_construct"


def test_rejects_delete_smuggled_into_cte():
    sql = "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d"
    assert codes(validate_sql(sql)) == "forbidden_construct"


def test_rejects_select_into_new_table():
    assert codes(validate_sql("SELECT * INTO t2 FROM t1")) == "forbidden_construct"


def test_rejects_insert_after_cte():
    assert codes(validate_sql("WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x")) == "unsupported_statement"


@pytest.mark.parametrize("sql", [
    "SELECT 1 UNION (INSERT INTO t VALUES (1))",
    "SELECT * FROM t WHERE a IN (DELETE FROM u)",
])
def test_rejects_write_inside_expression(sql):
    assert validate_sql(sql)["status"] == "error"


# --- denied functions ---------------------------------------------------------


@pytest.mark.parametrize("sql", [
    "SELECT load_extension('lib.so')",
    "SELECT LOAD_EXTENSION('lib.so')",
    "SELECT sleep(5)",
    "SELECT BENCHMARK(1, SLEEP(1))",
    "SELECT xp_cmdshell('dir')",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_sleep(1)",
    "SELECT lo_import('/tmp/x')",
    "SELECT dblink_exec('SELECT 1')",
    "SELECT sys_exec('ls')",
    "SELECT readfile('/tmp/x')",
    "SELECT eval('1+1')",
])
def test_rejects_dangerous_functions(sql):
    assert codes(validate_sql(sql)) == "forbidden_function"


def test_rejects_dangerous_function_nested_in_subquery():
    assert codes(validate_sql("SELECT (SELECT sleep(1))")) == "forbidden_function"


def test_rejects_dangerous_function_behind_alias():
    assert codes(validate_sql("SELECT sleep(1) AS s")) == "forbidden_function"


# --- multiple statements and malformed input ----------------------------------


@pytest.mark.parametrize("sql", [
    "SELECT 1; SELECT 2",
    "SELECT 1;DROP TABLE t",
    "SELECT 1; INSERT INTO t VALUES (1);",
])
def test_rejects_multiple_statements(sql):
    assert codes(validate_sql(sql)) == "multiple_statements"


@pytest.mark.parametrize("sql", [
    "SELCT 1",
    "SELECT FROM",
    "SELECT 1 UNION",
    "not sql at all",
    "SELECT * FROM t INTO OUTFILE '/tmp/x'",
])
def test_rejects_malformed_sql(sql):
    assert codes(validate_sql(sql)) == "syntax_error"


@pytest.mark.parametrize("sql", ["", "   ", "\n\t", "-- only a comment", "/* only a comment */"])
def test_rejects_empty_or_comment_only_input(sql):
    assert codes(validate_sql(sql)) == "empty_query"


@pytest.mark.parametrize("sql", [None, 123, 4.5, True, b"SELECT 1", ["SELECT 1"], {"sql": "SELECT 1"}])
def test_rejects_non_string_sql(sql):
    assert codes(validate_sql(sql)) == "invalid_input"


def test_rejects_oversized_sql():
    assert codes(validate_sql("SELECT 1 FROM t WHERE a = '" + "x" * MAX_SQL_LENGTH)) == "query_too_long"


def test_error_messages_are_bounded():
    result = validate_sql("SELCT " + "a" * 4000)
    assert len(result["error"]["message"]) <= MAX_MESSAGE_LENGTH


# --- dialect handling ---------------------------------------------------------


@pytest.mark.parametrize("dialect", [
    "bigquery", "duckdb", "tsql", "snowflake", "oracle", "redshift", "flink",
    "NOTAREALDB", "sqlite2", "", "   ", None, 12, ["sqlite"],
])
def test_rejects_unsupported_dialects(dialect):
    assert codes(validate_sql("SELECT 1", dialect)) == "unsupported_dialect"


@pytest.mark.parametrize("alias,canonical", [
    ("sqlite", "sqlite"), ("Sqlite3", "sqlite"), ("SQLITE", "sqlite"),
    ("postgres", "postgres"), ("postgresql", "postgres"), ("PG", "postgres"),
    ("mysql", "mysql"), ("MariaDB", "mysql"),
])
def test_dialect_aliases_resolve_to_canonical(alias, canonical):
    result = validate_sql("SELECT 1", alias)
    assert result["status"] == "ok" and result["dialect"] == canonical


def test_supported_dialects_are_three_engines():
    assert set(SUPPORTED_DIALECTS.values()) == {"sqlite", "postgres", "mysql"}


# --- result contract ----------------------------------------------------------


def test_ok_result_shape():
    result = validate_sql("SELECT a FROM t")
    assert RESULT_KEYS <= set(result)
    assert result["status"] == "ok" and result["valid"] is True
    assert result["error"] is None and result["errors"] == []
    assert result["dialect"] == "sqlite" and result["requested_dialect"] == "sqlite"


def test_error_result_shape_matches_agent1_error_dict():
    result = validate_sql("DELETE FROM t")
    assert RESULT_KEYS <= set(result)
    assert result["status"] == "error" and result["valid"] is False
    assert set(result["error"]) == {"type", "message"}
    assert result["errors"] == [result["error"]]
    assert result["normalized_sql"] is None and result["statement_kind"] is None


def test_checks_passed_lists_every_gate():
    result = validate_sql("SELECT a FROM t")
    assert result["checks_passed"] == [
        "input_is_text", "length_bounded", "dialect_supported", "parses",
        "single_statement", "read_only_statement_type", "no_forbidden_constructs",
        "no_forbidden_functions", "normalized_sql_revalidated",
    ]


def test_checks_passed_stops_at_first_failure():
    result = validate_sql("DELETE FROM t")
    assert result["checks_passed"] == [
        "input_is_text", "length_bounded", "dialect_supported", "parses", "single_statement",
    ]
    assert "read_only_statement_type" not in result["checks_passed"]


def test_normalized_sql_round_trips_through_validation():
    first = validate_sql("with x as (select a from t) select a from x", "postgres")
    assert first["status"] == "ok"
    second = validate_sql(first["normalized_sql"], "postgres")
    assert second["status"] == "ok"
    assert second["normalized_sql"] == first["normalized_sql"]


def test_normalized_sql_is_safe_to_execute_verbatim():
    result = validate_sql("SELECT 1; -- trailing")
    assert ";" not in result["normalized_sql"]
    assert "--" not in result["normalized_sql"]


@pytest.mark.parametrize("sql", [
    "", None, "DROP TABLE t", "SELECT 1; SELECT 2", "SELECT 1", "not sql",
    "SELECT load_extension('x')", "🏳️‍🌈 SELECT", "SELECT 'unterminated",
])
def test_never_raises_for_any_input(sql):
    result = validate_sql(sql, "sqlite")
    assert isinstance(result, dict)
    assert result["status"] in {"ok", "error"}
    assert isinstance(result["valid"], bool)
