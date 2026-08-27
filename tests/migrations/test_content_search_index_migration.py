from __future__ import annotations

import importlib
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.settings import get_settings
from app.models.db import Content
from app.repositories.content_search_expressions import CONTENT_SEARCH_DOCUMENT_SQL
from app.testing.postgres_harness import create_temporary_postgres_harness


def test_content_search_migration_uses_the_canonical_document() -> None:
    migration = importlib.import_module(
        "migrations.alembic.versions.20260825_01_align_content_search_index"
    )

    assert " ".join(migration.CONTENT_SEARCH_DOCUMENT_SQL.split()) == " ".join(
        CONTENT_SEARCH_DOCUMENT_SQL.split()
    )
    assert migration.SUMMARY_TITLE_TRGM_INDEX_NAME == "idx_contents_summary_title_trgm"
    assert migration.down_revision == "20260823_01"


def test_content_search_migration_recovers_failed_index_and_sanitizes_metadata(
    monkeypatch,
) -> None:
    """A retry must recover the exact failed production migration state."""
    harness = create_temporary_postgres_harness(
        schema_prefix="content_search_index_migration",
        tables=[Content.__table__],
    )
    malformed_metadata = (
        r'{"summary":{"title":"Broken\u0000 title",'
        r'"literal":"Keep \\u0000 text","overview":"Still searchable"}}'
    )
    clean_metadata = r'{"summary":{"title":"Already valid"}}'
    literal_escape_metadata = r'{"summary":{"title":"Literal \\u0000 text"}}'
    try:
        with harness.engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY NOT NULL)")
            )
            connection.execute(text("INSERT INTO alembic_version VALUES ('20260823_01')"))
            connection.exec_driver_sql(
                """
                INSERT INTO contents (
                    id, content_type, url, title, source, status, is_aggregate,
                    content_metadata, search_text, created_at
                ) VALUES (
                    1, 'article', 'https://example.com/broken', 'Fallback title', 'Example',
                    'completed', false, CAST(%s AS json), 'Broken title body', now()
                ), (
                    2, 'article', 'https://example.com/clean', 'Clean title', 'Example',
                    'completed', false, CAST(%s AS json), 'Clean title body', now()
                ), (
                    3, 'article', 'https://example.com/literal', 'Literal title', 'Example',
                    'completed', false, CAST(%s AS json), 'Literal escape body', now()
                )
                """,
                (malformed_metadata, clean_metadata, literal_escape_metadata),
            )
            connection.execute(
                text(
                    "CREATE INDEX idx_contents_search_document_gin ON contents USING GIN (("
                    "setweight(to_tsvector('english', COALESCE(title, '')), 'A') "
                    "|| setweight(to_tsvector('english', COALESCE(source, '')), 'B') "
                    "|| setweight(to_tsvector('english', COALESCE(search_text, '')), 'C')"
                    "))"
                )
            )

        with harness.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("DROP INDEX CONCURRENTLY idx_contents_search_document_gin"))
            with pytest.raises(DBAPIError):
                connection.execute(
                    text(
                        "CREATE INDEX CONCURRENTLY idx_contents_search_document_gin "
                        f"ON contents USING GIN ({CONTENT_SEARCH_DOCUMENT_SQL})"
                    )
                )

        with harness.engine.connect() as connection:
            failed_index = connection.execute(
                text(
                    """
                    SELECT i.indisvalid, i.indisready
                    FROM pg_class AS c
                    JOIN pg_index AS i ON i.indexrelid = c.oid
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE c.relname = 'idx_contents_search_document_gin'
                      AND n.nspname = current_schema()
                    """
                )
            ).one()
            assert tuple(failed_index) == (False, False)

        migration_database_url = harness.database_url.replace("%3D", "=")
        monkeypatch.setenv("DATABASE_URL", migration_database_url)
        get_settings.cache_clear()
        command.upgrade(Config("migrations/alembic.ini"), "20260825_01")

        with harness.engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("20260825_01")
            repaired_metadata_text = connection.execute(
                text("SELECT CAST(content_metadata AS text) FROM contents WHERE id = 1")
            ).scalar_one()
            repaired_metadata = json.loads(repaired_metadata_text)
            assert repaired_metadata["summary"] == {
                "literal": r"Keep \u0000 text",
                "overview": "Still searchable",
                "title": "Broken title",
            }
            assert (
                connection.execute(
                    text("SELECT CAST(content_metadata AS text) FROM contents WHERE id = 2")
                ).scalar_one()
                == clean_metadata
            )
            assert (
                connection.execute(
                    text("SELECT CAST(content_metadata AS text) FROM contents WHERE id = 3")
                ).scalar_one()
                == literal_escape_metadata
            )
            indexes = connection.execute(
                text(
                    """
                    SELECT c.relname, i.indisvalid, i.indisready
                    FROM pg_class AS c
                    JOIN pg_index AS i ON i.indexrelid = c.oid
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE c.relname IN (
                        'idx_contents_search_document_gin',
                        'idx_contents_summary_title_trgm'
                    )
                      AND n.nspname = current_schema()
                    ORDER BY c.relname
                    """
                )
            ).all()
            assert [tuple(row) for row in indexes] == [
                ("idx_contents_search_document_gin", True, True),
                ("idx_contents_summary_title_trgm", True, True),
            ]
    finally:
        get_settings.cache_clear()
        harness.close()


def test_content_search_index_build_retries_a_concurrent_old_app_write(monkeypatch) -> None:
    """A malformed write from the still-live old app must not fail the rollout again."""
    harness = create_temporary_postgres_harness(
        schema_prefix="content_search_index_concurrent_write",
        tables=[Content.__table__],
    )
    migration = importlib.import_module(
        "migrations.alembic.versions.20260825_01_align_content_search_index"
    )
    original_replace_index = migration._replace_index_concurrently
    primary_build_calls = 0

    def replace_index_after_concurrent_old_app_write(
        *,
        index_name: str,
        replacement_name: str,
        expression_sql: str,
    ) -> None:
        nonlocal primary_build_calls
        primary_build_calls += 1
        if primary_build_calls == 1:
            with harness.engine.begin() as writer:
                writer.exec_driver_sql(
                    """
                    INSERT INTO contents (
                        id, content_type, url, title, source, status, is_aggregate,
                        content_metadata, search_text, created_at
                    ) VALUES (
                        22, 'article', 'https://example.com/concurrent', 'Concurrent title',
                        'Example', 'completed', false, CAST(%s AS json),
                        'Concurrent title body', now()
                    )
                    """,
                    (r'{"summary":{"title":"Concurrent\u0000 title"}}',),
                )
        original_replace_index(
            index_name=index_name,
            replacement_name=replacement_name,
            expression_sql=expression_sql,
        )

    monkeypatch.setattr(
        migration,
        "_replace_index_concurrently",
        replace_index_after_concurrent_old_app_write,
    )
    try:
        with harness.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO contents (
                        id, content_type, url, title, source, status, is_aggregate,
                        content_metadata, search_text, created_at
                    ) VALUES (
                        21, 'article', 'https://example.com/existing', 'Existing title',
                        'Example', 'completed', false,
                        CAST('{"summary":{"title":"Existing summary"}}' AS json),
                        'Existing title body', now()
                    )
                    """
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX idx_contents_search_document_gin ON contents USING GIN (("
                    "setweight(to_tsvector('english', COALESCE(title, '')), 'A') "
                    "|| setweight(to_tsvector('english', COALESCE(source, '')), 'B') "
                    "|| setweight(to_tsvector('english', COALESCE(search_text, '')), 'C')"
                    "))"
                )
            )

        with harness.engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with context.begin_transaction(), Operations.context(context):
                migration._replace_index_with_metadata_recovery(
                    index_name=migration.INDEX_NAME,
                    replacement_name=migration.REPLACEMENT_INDEX_NAME,
                    expression_sql=migration.CONTENT_SEARCH_DOCUMENT_SQL,
                )

        with harness.engine.connect() as connection:
            assert connection.execute(
                text("SELECT content_metadata -> 'summary' ->> 'title' FROM contents WHERE id = 22")
            ).scalar_one() == ("Concurrent title")
            state = connection.execute(
                text(
                    """
                    SELECT i.indisvalid, i.indisready
                    FROM pg_class AS c
                    JOIN pg_index AS i ON i.indexrelid = c.oid
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE c.relname = 'idx_contents_search_document_gin'
                      AND n.nspname = current_schema()
                    """
                )
            ).one()
            assert tuple(state) == (True, True)
            assert primary_build_calls == 2
    finally:
        harness.close()


def test_content_search_index_build_cleans_up_after_retry_exhaustion(monkeypatch) -> None:
    """The last failed concurrent build must not leave a maintained invalid index."""
    migration = importlib.import_module(
        "migrations.alembic.versions.20260825_01_align_content_search_index"
    )
    build_calls = 0
    sanitization_calls = 0
    executed_statements: list[str] = []

    class UnsupportedNulEscape(Exception):
        sqlstate = migration.UNTRANSLATABLE_CHARACTER_SQLSTATE

    def fail_index_build(**_kwargs: str) -> None:
        nonlocal build_calls
        build_calls += 1
        raise DBAPIError(
            statement=None,
            params=None,
            orig=UnsupportedNulEscape(r"unsupported Unicode escape sequence: \u0000"),
            connection_invalidated=False,
        )

    def record_sanitization() -> int:
        nonlocal sanitization_calls
        sanitization_calls += 1
        return 0

    @contextmanager
    def autocommit_block():
        yield

    fake_op = SimpleNamespace(
        get_context=lambda: SimpleNamespace(autocommit_block=autocommit_block),
        execute=executed_statements.append,
    )
    monkeypatch.setattr(migration, "_replace_index_concurrently", fail_index_build)
    monkeypatch.setattr(migration, "_sanitize_malformed_content_metadata", record_sanitization)
    monkeypatch.setattr(migration, "op", fake_op)

    with pytest.raises(DBAPIError):
        migration._replace_index_with_metadata_recovery(
            index_name=migration.INDEX_NAME,
            replacement_name=migration.REPLACEMENT_INDEX_NAME,
            expression_sql=migration.CONTENT_SEARCH_DOCUMENT_SQL,
        )

    assert build_calls == migration.MAX_INDEX_BUILD_ATTEMPTS
    assert sanitization_calls == migration.MAX_INDEX_BUILD_ATTEMPTS - 1
    assert (
        executed_statements
        == [f"DROP INDEX CONCURRENTLY IF EXISTS {migration.REPLACEMENT_INDEX_NAME}"]
        * migration.MAX_INDEX_BUILD_ATTEMPTS
    )


def test_content_search_migration_reuses_a_valid_replacement_index(monkeypatch) -> None:
    """A retry should finish the swap instead of rebuilding a completed replacement."""
    harness = create_temporary_postgres_harness(
        schema_prefix="content_search_index_replacement",
        tables=[Content.__table__],
    )
    try:
        with harness.engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY NOT NULL)")
            )
            connection.execute(text("INSERT INTO alembic_version VALUES ('20260823_01')"))
            connection.execute(
                text(
                    """
                    INSERT INTO contents (
                        id, content_type, url, title, source, status, is_aggregate,
                        content_metadata, search_text, created_at
                    ) VALUES (
                        11, 'article', 'https://example.com/first', 'Duplicate title',
                        'Example', 'completed', false,
                        CAST('{"summary":{"title":"First summary"}}' AS json),
                        'First body', now()
                    ), (
                        12, 'article', 'https://example.com/second', 'Duplicate title',
                        'Example', 'completed', false,
                        CAST('{"summary":{"title":"Second summary"}}' AS json),
                        'Second body', now()
                    )
                    """
                )
            )

        with harness.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(
                text(
                    "CREATE INDEX CONCURRENTLY idx_contents_search_document_gin_rebuild "
                    f"ON contents USING GIN ({CONTENT_SEARCH_DOCUMENT_SQL})"
                )
            )
            replacement_oid = connection.execute(
                text(
                    """
                    SELECT c.oid
                    FROM pg_class AS c
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE c.relname = 'idx_contents_search_document_gin_rebuild'
                      AND n.nspname = current_schema()
                    """
                )
            ).scalar_one()
            with pytest.raises(DBAPIError):
                connection.execute(
                    text(
                        "CREATE UNIQUE INDEX CONCURRENTLY idx_contents_search_document_gin "
                        "ON contents (title)"
                    )
                )

        migration_database_url = harness.database_url.replace("%3D", "=")
        monkeypatch.setenv("DATABASE_URL", migration_database_url)
        get_settings.cache_clear()
        command.upgrade(Config("migrations/alembic.ini"), "20260825_01")

        with harness.engine.connect() as connection:
            canonical_index = connection.execute(
                text(
                    """
                    SELECT c.oid, i.indisvalid, i.indisready
                    FROM pg_class AS c
                    JOIN pg_index AS i ON i.indexrelid = c.oid
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE c.relname = 'idx_contents_search_document_gin'
                      AND n.nspname = current_schema()
                    """
                )
            ).one()
            assert tuple(canonical_index) == (replacement_oid, True, True)
            assert (
                connection.execute(
                    text("SELECT to_regclass('idx_contents_search_document_gin_rebuild')")
                ).scalar_one()
                is None
            )
    finally:
        get_settings.cache_clear()
        harness.close()
