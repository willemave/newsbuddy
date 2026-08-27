"""Align the content search GIN index with the canonical weighted document.

Revision ID: 20260825_01
Revises: 20260823_01
Create Date: 2026-08-25
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.exc import DBAPIError

revision: str = "20260825_01"
down_revision: str | None = "20260823_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "idx_contents_search_document_gin"
SUMMARY_TITLE_TRGM_INDEX_NAME = "idx_contents_summary_title_trgm"
REPLACEMENT_INDEX_NAME = f"{INDEX_NAME}_rebuild"
LEGACY_REPLACEMENT_INDEX_NAME = f"{INDEX_NAME}_legacy_rebuild"
REPLACEMENT_SUMMARY_TITLE_TRGM_INDEX_NAME = f"{SUMMARY_TITLE_TRGM_INDEX_NAME}_rebuild"
ESCAPED_NUL_JSON_PATTERN = r"%\\u0000%"
UNTRANSLATABLE_CHARACTER_SQLSTATE = "22P05"
MAX_INDEX_BUILD_ATTEMPTS = 3
_ALLOWED_CONTROL_CHARACTERS = {"\n", "\r", "\t"}
CONTENT_SEARCH_DOCUMENT_SQL = """(
    setweight(to_tsvector('english', COALESCE(content_metadata -> 'summary' ->> 'title', '')), 'A')
    || setweight(to_tsvector('english', COALESCE(title, '')), 'B')
    || setweight(to_tsvector('english', COALESCE(source, '')), 'C')
    || setweight(to_tsvector('english', COALESCE(search_text, '')), 'D')
)"""


def _strip_disallowed_control_characters(value: str) -> str:
    return "".join(
        character
        for character in value
        if ord(character) >= 32 or character in _ALLOWED_CONTROL_CHARACTERS
    )


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _strip_disallowed_control_characters(str(key)): _sanitize_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, str):
        return _strip_disallowed_control_characters(value)
    return value


def _sanitize_malformed_content_metadata() -> int:
    """Remove decoded NUL/control characters without changing literal escape text."""
    bind = op.get_bind()
    rows = (
        bind.execute(
            sa.text(
                """
                SELECT id, CAST(content_metadata AS text) AS metadata_text
                FROM contents
                WHERE CAST(content_metadata AS text) LIKE :pattern
                ORDER BY id
                FOR UPDATE
                """
            ),
            {"pattern": ESCAPED_NUL_JSON_PATTERN},
        )
        .mappings()
        .all()
    )
    updated_count = 0
    for row in rows:
        parsed = json.loads(str(row["metadata_text"]))
        sanitized = _sanitize_json_value(parsed)
        if sanitized == parsed:
            continue
        bind.execute(
            sa.text(
                """
                UPDATE contents
                SET content_metadata = CAST(:content_metadata AS json)
                WHERE id = :content_id
                """
            ),
            {
                "content_id": int(row["id"]),
                "content_metadata": json.dumps(
                    sanitized,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
        )
        updated_count += 1
    return updated_count


def _replace_index_concurrently(
    *,
    index_name: str,
    replacement_name: str,
    expression_sql: str,
) -> None:
    """Build a valid replacement before removing a working or invalid target index."""
    if not _index_is_valid_and_ready(replacement_name):
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {replacement_name}")
        op.execute(
            f"CREATE INDEX CONCURRENTLY {replacement_name} ON contents USING GIN ({expression_sql})"
        )
    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")
    op.execute(f"ALTER INDEX {replacement_name} RENAME TO {index_name}")


def _index_is_valid_and_ready(index_name: str) -> bool:
    state = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT index.indisvalid, index.indisready
                FROM pg_class AS relation
                JOIN pg_index AS index ON index.indexrelid = relation.oid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE relation.relname = :index_name
                  AND namespace.nspname = current_schema()
                """
            ),
            {"index_name": index_name},
        )
        .mappings()
        .one_or_none()
    )
    return bool(state and state["indisvalid"] and state["indisready"])


def _is_unsupported_nul_escape(error: DBAPIError) -> bool:
    return getattr(
        error.orig, "sqlstate", None
    ) == UNTRANSLATABLE_CHARACTER_SQLSTATE and r"\u0000" in str(error.orig)


def _replace_index_with_metadata_recovery(
    *,
    index_name: str,
    replacement_name: str,
    expression_sql: str,
) -> None:
    """Retry a concurrent build if an old app write introduces another NUL."""
    for attempt in range(MAX_INDEX_BUILD_ATTEMPTS):
        try:
            with op.get_context().autocommit_block():
                _replace_index_concurrently(
                    index_name=index_name,
                    replacement_name=replacement_name,
                    expression_sql=expression_sql,
                )
            return
        except DBAPIError as error:
            if not _is_unsupported_nul_escape(error):
                raise
            # A failed concurrent build can be marked ready and maintained by
            # PostgreSQL. Remove it before updating the row that broke its expression.
            with op.get_context().autocommit_block():
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {replacement_name}")
            if attempt == MAX_INDEX_BUILD_ATTEMPTS - 1:
                raise
            _sanitize_malformed_content_metadata()


LEGACY_SEARCH_DOCUMENT_SQL = """(
    setweight(to_tsvector('english', COALESCE(title, '')), 'A')
    || setweight(to_tsvector('english', COALESCE(source, '')), 'B')
    || setweight(to_tsvector('english', COALESCE(search_text, '')), 'C')
)"""


def upgrade() -> None:
    """Rebuild the index without holding a write lock for the duration of the scan."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    _sanitize_malformed_content_metadata()
    _replace_index_with_metadata_recovery(
        index_name=INDEX_NAME,
        replacement_name=REPLACEMENT_INDEX_NAME,
        expression_sql=CONTENT_SEARCH_DOCUMENT_SQL,
    )
    _replace_index_with_metadata_recovery(
        index_name=SUMMARY_TITLE_TRGM_INDEX_NAME,
        replacement_name=REPLACEMENT_SUMMARY_TITLE_TRGM_INDEX_NAME,
        expression_sql=(
            "(COALESCE(content_metadata -> 'summary' ->> 'title', '')) public.gin_trgm_ops"
        ),
    )


def downgrade() -> None:
    """Restore the legacy weighted index."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        _replace_index_concurrently(
            index_name=INDEX_NAME,
            replacement_name=LEGACY_REPLACEMENT_INDEX_NAME,
            expression_sql=LEGACY_SEARCH_DOCUMENT_SQL,
        )
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {REPLACEMENT_INDEX_NAME}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {SUMMARY_TITLE_TRGM_INDEX_NAME}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {REPLACEMENT_SUMMARY_TITLE_TRGM_INDEX_NAME}")
