"""add_content_fts_search

Create SQLite FTS5 table and triggers for content search.

Revision ID: 9b2a6f9e5c1d
Revises: 1f5c42ea0015
Create Date: 2026-01-02 00:00:00

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "9b2a6f9e5c1d"
down_revision: str | None = "1f5c42ea0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create FTS table and triggers for content search."""
    conn = op.get_bind()
    if conn.dialect.name != "sqlite":
        return

    conn.execute(
        text(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(
                title,
                source,
                summary,
                search_text
            )
            """
        )
    )

    conn.execute(
        text(
            """
            CREATE TRIGGER IF NOT EXISTS content_fts_ai AFTER INSERT ON contents BEGIN
                INSERT INTO content_fts(rowid, title, source, summary, search_text)
                VALUES (
                    new.id,
                    new.title,
                    new.source,
                    COALESCE(json_extract(new.content_metadata, '$.summary'), ''),
                    COALESCE(new.search_text, '')
                );
            END;
            """
        )
    )

    conn.execute(
        text(
            """
            CREATE TRIGGER IF NOT EXISTS content_fts_au AFTER UPDATE ON contents BEGIN
                DELETE FROM content_fts WHERE rowid = old.id;
                INSERT INTO content_fts(rowid, title, source, summary, search_text)
                VALUES (
                    new.id,
                    new.title,
                    new.source,
                    COALESCE(json_extract(new.content_metadata, '$.summary'), ''),
                    COALESCE(new.search_text, '')
                );
            END;
            """
        )
    )

    conn.execute(
        text(
            """
            CREATE TRIGGER IF NOT EXISTS content_fts_ad AFTER DELETE ON contents BEGIN
                DELETE FROM content_fts WHERE rowid = old.id;
            END;
            """
        )
    )

    conn.execute(
        text(
            """
            INSERT OR REPLACE INTO content_fts(rowid, title, source, summary, search_text)
            SELECT
                id,
                title,
                source,
                COALESCE(json_extract(content_metadata, '$.summary'), ''),
                COALESCE(search_text, '')
            FROM contents
            """
        )
    )


def downgrade() -> None:
    """Drop FTS table and triggers."""
    conn = op.get_bind()
    if conn.dialect.name != "sqlite":
        return

    conn.execute(text("DROP TRIGGER IF EXISTS content_fts_ai"))
    conn.execute(text("DROP TRIGGER IF EXISTS content_fts_au"))
    conn.execute(text("DROP TRIGGER IF EXISTS content_fts_ad"))
    conn.execute(text("DROP TABLE IF EXISTS content_fts"))
