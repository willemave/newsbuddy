"""Database-dialect behavior for admin read-only diagnostics."""

from __future__ import annotations

from pathlib import Path

from admin.remote_ops import RemoteContext, db_explain, db_query


def _context(database_url: str, tmp_path: Path) -> RemoteContext:
    return RemoteContext(
        database_url=database_url,
        logs_dir=tmp_path,
        service_log_dir=tmp_path,
    )


def test_db_explain_uses_sqlite_query_plan_prefix(tmp_path: Path) -> None:
    database_path = tmp_path / "admin.sqlite3"
    context = _context(f"sqlite:///{database_path}", tmp_path)

    result = db_explain(context, sql="SELECT 1")

    assert result["sql"] == "EXPLAIN QUERY PLAN SELECT 1"


def test_db_query_supports_sqlite_without_postgresql_transaction_sql(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "admin.sqlite3"
    context = _context(f"sqlite:///{database_path}", tmp_path)

    result = db_query(context, sql="SELECT 1 AS value")

    assert result["rows"] == [{"value": 1}]


def test_db_explain_preserves_explicit_native_explain(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def execute_sql(database_url: str, sql: str, *, limit: int):  # noqa: ANN001
        del database_url, limit
        captured["sql"] = sql
        return [], []

    monkeypatch.setattr("admin.remote_ops._execute_sql", execute_sql)
    context = _context("postgresql+psycopg://user:password@localhost/newsly", tmp_path)

    result = db_explain(
        context,
        sql="EXPLAIN (ANALYZE, BUFFERS) SELECT 1",
    )

    assert captured["sql"] == "EXPLAIN (ANALYZE, BUFFERS) SELECT 1"
    assert result["sql"] == captured["sql"]


def test_db_explain_uses_postgresql_prefix(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def execute_sql(database_url: str, sql: str, *, limit: int):  # noqa: ANN001
        del database_url, limit
        captured["sql"] = sql
        return [], []

    monkeypatch.setattr("admin.remote_ops._execute_sql", execute_sql)
    context = _context("postgresql+psycopg://user:password@localhost/newsly", tmp_path)

    result = db_explain(context, sql="SELECT 1")

    assert captured["sql"] == "EXPLAIN SELECT 1"
    assert result["sql"] == captured["sql"]
