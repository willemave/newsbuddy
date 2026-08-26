"""Align the content search GIN index with the canonical weighted document.

Revision ID: 20260825_01
Revises: 20260823_01
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260825_01"
down_revision: str | None = "20260823_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "idx_contents_search_document_gin"
SUMMARY_TITLE_TRGM_INDEX_NAME = "idx_contents_summary_title_trgm"
CONTENT_SEARCH_DOCUMENT_SQL = """(
    setweight(to_tsvector('english', COALESCE(content_metadata -> 'summary' ->> 'title', '')), 'A')
    || setweight(to_tsvector('english', COALESCE(title, '')), 'B')
    || setweight(to_tsvector('english', COALESCE(source, '')), 'C')
    || setweight(to_tsvector('english', COALESCE(search_text, '')), 'D')
)"""
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
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
        op.execute(
            f"CREATE INDEX CONCURRENTLY {INDEX_NAME} "
            f"ON contents USING GIN ({CONTENT_SEARCH_DOCUMENT_SQL})"
        )
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {SUMMARY_TITLE_TRGM_INDEX_NAME} "
            "ON contents USING GIN "
            "((COALESCE(content_metadata -> 'summary' ->> 'title', '')) "
            "public.gin_trgm_ops)"
        )


def downgrade() -> None:
    """Restore the legacy weighted index."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {SUMMARY_TITLE_TRGM_INDEX_NAME}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
        op.execute(
            f"CREATE INDEX CONCURRENTLY {INDEX_NAME} "
            f"ON contents USING GIN ({LEGACY_SEARCH_DOCUMENT_SQL})"
        )
