"""Add the indexes used by News relation candidate retrieval.

Revision ID: 20260815_01
Revises: 20260807_02
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "20260815_01"
down_revision: str | None = "20260807_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "idx_news_items_relation_title_document_gin"
STORY_KEY_INDEX_NAME = "idx_news_items_relation_story_key_hash"
ITEM_KEY_INDEX_NAME = "idx_news_items_relation_item_key_hash"
EXTERNAL_KEY_INDEX_NAME = "idx_news_items_relation_external_key"


def upgrade() -> None:
    op.execute(
        text(
            f"""
            CREATE INDEX IF NOT EXISTS {INDEX_NAME}
            ON news_items
            USING GIN (
                (
                    setweight(to_tsvector('english', COALESCE(raw_metadata -> 'summary' ->> 'title', '')), 'A')
                    || setweight(to_tsvector('english', COALESCE(raw_metadata -> 'article' ->> 'title', '')), 'A')
                    || setweight(
                        to_tsvector(
                            'english',
                            COALESCE(raw_metadata -> 'cluster' ->> 'related_titles', '')
                        ),
                        'B'
                    )
                )
            )
            """
        )
    )
    op.execute(
        text(
            f"""
            CREATE INDEX IF NOT EXISTS {STORY_KEY_INDEX_NAME}
            ON news_items
            USING HASH ((COALESCE(canonical_story_url, article_url)))
            """
        )
    )
    op.execute(
        text(
            f"""
            CREATE INDEX IF NOT EXISTS {ITEM_KEY_INDEX_NAME}
            ON news_items
            USING HASH ((COALESCE(canonical_item_url, discussion_url)))
            """
        )
    )
    op.execute(
        text(
            f"""
            CREATE INDEX IF NOT EXISTS {EXTERNAL_KEY_INDEX_NAME}
            ON news_items (platform, source_external_id)
            """
        )
    )


def downgrade() -> None:
    op.execute(text(f"DROP INDEX IF EXISTS {EXTERNAL_KEY_INDEX_NAME}"))
    op.execute(text(f"DROP INDEX IF EXISTS {ITEM_KEY_INDEX_NAME}"))
    op.execute(text(f"DROP INDEX IF EXISTS {STORY_KEY_INDEX_NAME}"))
    op.execute(text(f"DROP INDEX IF EXISTS {INDEX_NAME}"))
