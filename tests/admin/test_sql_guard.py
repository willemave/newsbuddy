"""Tests for read-only SQL validation."""

from __future__ import annotations

import pytest

from admin.sql_guard import validate_readonly_sql


def test_validate_readonly_sql_allows_select():
    assert validate_readonly_sql("SELECT * FROM users;") == "SELECT * FROM users"


@pytest.mark.parametrize(
    "statement",
    [
        "EXPLAIN SELECT * FROM users",
        "EXPLAIN ANALYZE SELECT * FROM users",
        "EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM users",
        "EXPLAIN QUERY PLAN SELECT * FROM users",
    ],
)
def test_validate_readonly_sql_allows_database_native_explain(statement: str):
    assert validate_readonly_sql(statement) == statement


def test_validate_readonly_sql_rejects_multi_statement():
    with pytest.raises(ValueError, match="single-statement"):
        validate_readonly_sql("SELECT * FROM users; SELECT * FROM contents")


def test_validate_readonly_sql_rejects_mutation_keyword():
    with pytest.raises(ValueError, match="Forbidden SQL keyword"):
        validate_readonly_sql(
            "WITH doomed AS (DELETE FROM users RETURNING id) SELECT * FROM doomed"
        )


def test_validate_readonly_sql_rejects_explain_of_non_query_statement():
    with pytest.raises(ValueError, match="must target"):
        validate_readonly_sql("EXPLAIN CALL refresh_materialized_views()")
