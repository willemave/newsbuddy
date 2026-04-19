"""Add PostgreSQL content FTS index.

Revision ID: 20260409_02
Revises: 20260409_01
Create Date: 2026-04-09
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "20260409_02"
down_revision: str | None = "20260409_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "idx_contents_search_document_gin"
TITLE_TRGM_INDEX_NAME = "idx_contents_title_trgm"
SOURCE_TRGM_INDEX_NAME = "idx_contents_source_trgm"


def upgrade() -> None:
    """Create the weighted GIN index used by PostgreSQL search."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    op.execute(
        text(
            f"""
            CREATE INDEX IF NOT EXISTS {INDEX_NAME}
            ON contents
            USING GIN (
                (
                    setweight(to_tsvector('english', COALESCE(title, '')), 'A')
                    || setweight(to_tsvector('english', COALESCE(source, '')), 'B')
                    || setweight(to_tsvector('english', COALESCE(search_text, '')), 'C')
                )
            )
            """
        )
    )
    op.execute(
        text(
            f"""
            CREATE INDEX IF NOT EXISTS {TITLE_TRGM_INDEX_NAME}
            ON contents
            USING GIN (title gin_trgm_ops)
            """
        )
    )
    op.execute(
        text(
            f"""
            CREATE INDEX IF NOT EXISTS {SOURCE_TRGM_INDEX_NAME}
            ON contents
            USING GIN (source gin_trgm_ops)
            """
        )
    )


def downgrade() -> None:
    """Drop the PostgreSQL search index."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(text(f"DROP INDEX IF EXISTS {SOURCE_TRGM_INDEX_NAME}"))
    op.execute(text(f"DROP INDEX IF EXISTS {TITLE_TRGM_INDEX_NAME}"))
    op.execute(text(f"DROP INDEX IF EXISTS {INDEX_NAME}"))
