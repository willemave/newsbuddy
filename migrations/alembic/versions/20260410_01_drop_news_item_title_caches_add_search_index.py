"""Drop news-item title cache columns and add PostgreSQL search indexes.

Revision ID: 20260410_01
Revises: 20260409_03
Create Date: 2026-04-10
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "20260410_01"
down_revision: str | None = "20260409_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEARCH_INDEX_NAME = "idx_news_items_search_document_gin"
SUMMARY_TITLE_TRGM_INDEX_NAME = "idx_news_items_summary_title_trgm"
ARTICLE_TITLE_TRGM_INDEX_NAME = "idx_news_items_article_title_trgm"


def _clean_title(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _set_nested_title(raw_metadata: Any, section_name: str, title: Any) -> dict[str, Any]:
    updated = _mapping(raw_metadata)
    section = _mapping(updated.get(section_name))
    cleaned = _clean_title(title)
    if cleaned:
        section["title"] = cleaned
    else:
        section.pop("title", None)
    if section:
        updated[section_name] = section
    elif section_name in updated:
        updated.pop(section_name, None)
    return updated


def _backfill_metadata_titles_from_columns() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        text("SELECT id, raw_metadata, article_title, summary_title FROM news_items")
    ).mappings()
    for row in rows:
        updated_metadata = _set_nested_title(row["raw_metadata"], "article", row["article_title"])
        updated_metadata = _set_nested_title(updated_metadata, "summary", row["summary_title"])
        if updated_metadata == (row["raw_metadata"] or {}):
            continue
        bind.execute(
            sa.text(
                "UPDATE news_items SET raw_metadata = :raw_metadata WHERE id = :row_id"
            ).bindparams(
                sa.bindparam("raw_metadata", type_=sa.JSON()),
                sa.bindparam("row_id", type_=sa.Integer()),
            ),
            {"raw_metadata": updated_metadata, "row_id": row["id"]},
        )


def _backfill_columns_from_metadata() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT id, raw_metadata FROM news_items")).mappings()
    for row in rows:
        metadata = _mapping(row["raw_metadata"])
        article_title = _clean_title(_mapping(metadata.get("article")).get("title"))
        summary_title = _clean_title(_mapping(metadata.get("summary")).get("title"))
        bind.execute(
            text(
                """
                UPDATE news_items
                SET article_title = :article_title,
                    summary_title = :summary_title
                WHERE id = :row_id
                """
            ).bindparams(
                sa.bindparam("article_title", type_=sa.String(length=500)),
                sa.bindparam("summary_title", type_=sa.Text()),
                sa.bindparam("row_id", type_=sa.Integer()),
            ),
            {
                "article_title": article_title,
                "summary_title": summary_title,
                "row_id": row["id"],
            },
        )


def _create_postgres_indexes() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    op.execute(
        text(
            f"""
            CREATE INDEX IF NOT EXISTS {SEARCH_INDEX_NAME}
            ON news_items
            USING GIN (
                (
                    setweight(to_tsvector('english', COALESCE(raw_metadata -> 'summary' ->> 'title', '')), 'A')
                    || setweight(to_tsvector('english', COALESCE(raw_metadata -> 'article' ->> 'title', '')), 'B')
                    || setweight(to_tsvector('english', COALESCE(summary_text, '')), 'C')
                    || setweight(
                        to_tsvector(
                            'english',
                            COALESCE(source_label, '')
                            || ' '
                            || COALESCE(article_domain, '')
                            || ' '
                            || COALESCE(raw_metadata -> 'cluster' ->> 'related_titles', '')
                        ),
                        'D'
                    )
                )
            )
            """
        )
    )
    op.execute(
        text(
            f"""
            CREATE INDEX IF NOT EXISTS {SUMMARY_TITLE_TRGM_INDEX_NAME}
            ON news_items
            USING GIN ((COALESCE(raw_metadata -> 'summary' ->> 'title', '')) gin_trgm_ops)
            """
        )
    )
    op.execute(
        text(
            f"""
            CREATE INDEX IF NOT EXISTS {ARTICLE_TITLE_TRGM_INDEX_NAME}
            ON news_items
            USING GIN ((COALESCE(raw_metadata -> 'article' ->> 'title', '')) gin_trgm_ops)
            """
        )
    )


def _drop_postgres_indexes() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(text(f"DROP INDEX IF EXISTS {ARTICLE_TITLE_TRGM_INDEX_NAME}"))
    op.execute(text(f"DROP INDEX IF EXISTS {SUMMARY_TITLE_TRGM_INDEX_NAME}"))
    op.execute(text(f"DROP INDEX IF EXISTS {SEARCH_INDEX_NAME}"))


def upgrade() -> None:
    """Backfill metadata titles, drop cache columns, and add news-item search indexes."""
    _backfill_metadata_titles_from_columns()
    with op.batch_alter_table("news_items") as batch_op:
        batch_op.drop_column("summary_title")
        batch_op.drop_column("article_title")
    _create_postgres_indexes()


def downgrade() -> None:
    """Restore dropped columns and repopulate them from canonical metadata titles."""
    _drop_postgres_indexes()
    with op.batch_alter_table("news_items") as batch_op:
        batch_op.add_column(sa.Column("article_title", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("summary_title", sa.Text(), nullable=True))
    _backfill_columns_from_metadata()
