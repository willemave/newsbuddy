"""add feed and read-status indexes

Revision ID: 20260609_01
Revises: 20260602_01
Create Date: 2026-06-09 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260609_01"
down_revision: str | None = "20260602_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add indexes for recently-read and feed keyset list queries."""
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_content_read_user_read_at
        ON content_read_status (user_id, read_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_contents_feed_sort_timestamp_id
        ON contents (
            COALESCE(publication_date, processed_at, created_at) DESC,
            id DESC
        )
        """
    )


def downgrade() -> None:
    """Remove feed/read-status indexes."""
    op.execute("DROP INDEX IF EXISTS idx_contents_feed_sort_timestamp_id")
    op.execute("DROP INDEX IF EXISTS idx_content_read_user_read_at")
