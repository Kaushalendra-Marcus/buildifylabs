"""Regression suite for sanitize_sql() (specs/05 §6 second checkbox).

Every write/DDL attempt - including ones smuggled inside CTEs or subqueries -
must be rejected. These tests pin the sanitizer's existing behavior so future
edits can't silently weaken it.
"""

import pytest
from fastapi import HTTPException

from app.middlewares.sql_sanitizer import sanitize_sql


def test_valid_select_passes():
    query = "SELECT id, name FROM t LIMIT 100"
    assert sanitize_sql(query) == query


def test_valid_select_with_semicolon_passes():
    assert sanitize_sql("SELECT id FROM t LIMIT 100;") == "SELECT id FROM t LIMIT 100;"


def test_union_select_passes():
    assert sanitize_sql("SELECT id FROM t UNION SELECT id FROM u LIMIT 10")


def test_cte_select_passes():
    assert sanitize_sql("WITH cte AS (SELECT id FROM t) SELECT id FROM cte LIMIT 1")


def test_empty_query():
    with pytest.raises(HTTPException) as exc:
        sanitize_sql("   ")
    assert exc.value.status_code == 400


def test_query_too_long():
    with pytest.raises(HTTPException) as exc:
        sanitize_sql("SELECT " + "a" * 1000 + " FROM t LIMIT 1")
    assert exc.value.status_code == 400


def test_missing_limit():
    with pytest.raises(HTTPException) as exc:
        sanitize_sql("SELECT id FROM t")
    assert exc.value.status_code == 403


def test_subquery_top_level_rejected():
    # A parenthesized SELECT parses as a top-level Subquery, not a Select.
    with pytest.raises(HTTPException) as exc:
        sanitize_sql("(SELECT id FROM t LIMIT 1)")
    assert exc.value.status_code == 403


def test_two_statements_rejected():
    with pytest.raises(HTTPException) as exc:
        sanitize_sql("SELECT id FROM t; SELECT x FROM u")
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "query",
    [
        "INSERT INTO t (id) VALUES (1) RETURNING id LIMIT 1",
        "UPDATE t SET id = 1 WHERE id = 2 LIMIT 1",
        "DELETE FROM t WHERE id = 1 LIMIT 1",
        "DROP TABLE t LIMIT 1",
        "CREATE TABLE t (id INT) LIMIT 1",
        "ALTER TABLE t ADD COLUMN x INT LIMIT 1",
        "TRUNCATE TABLE t LIMIT 1",
    ],
)
def test_write_and_ddl_rejected(query):
    with pytest.raises(HTTPException) as exc:
        sanitize_sql(query)
    # 400 = rejected at parse time (invalid SQL), 403 = rejected by the AST
    # walk (parses but is a write/DDL statement). Either is a hard rejection.
    assert exc.value.status_code in (400, 403)


@pytest.mark.parametrize(
    "query",
    [
        "WITH x AS (DELETE FROM t RETURNING id) SELECT id FROM x LIMIT 1",
        "WITH x AS (INSERT INTO t (id) VALUES (1) RETURNING id) SELECT id FROM x LIMIT 1",
        "WITH x AS (UPDATE t SET id = 1 RETURNING id) SELECT id FROM x LIMIT 1",
    ],
)
def test_write_smuggled_in_cte_rejected(query):
    with pytest.raises(HTTPException) as exc:
        sanitize_sql(query)
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "query",
    [
        "SELECT pg_sleep(10) LIMIT 1",
        "SELECT pg_read_file('/etc/passwd') LIMIT 1",
        "SELECT dblink_connect('x') LIMIT 1",
        "SELECT lo_export('/etc/passwd') LIMIT 1",
        "SELECT id FROM (SELECT pg_sleep(5) AS id) q LIMIT 1",
        "SELECT pg_terminate_backend(42) LIMIT 1",
    ],
)
def test_forbidden_functions_rejected(query):
    with pytest.raises(HTTPException) as exc:
        sanitize_sql(query)
    assert exc.value.status_code == 403
