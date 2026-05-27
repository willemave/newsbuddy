"""add discussion summary tracking

Revision ID: 20260525_01
Revises: 20260519_01
Create Date: 2026-05-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260525_01"
down_revision: str | None = "20260519_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "news_item_discussions",
        sa.Column("summary_input_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "news_item_discussions",
        sa.Column("summary_comment_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "news_item_discussions",
        sa.Column("summary_comment_fingerprints", sa.JSON(), nullable=True),
    )
    op.add_column(
        "news_item_discussions",
        sa.Column("summary_seen_input_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "news_item_discussions",
        sa.Column("summary_seen_comment_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "news_item_discussions",
        sa.Column("summary_seen_comment_fingerprints", sa.JSON(), nullable=True),
    )
    op.add_column(
        "news_item_discussions",
        sa.Column(
            "summary_incremental_update_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.alter_column(
        "news_item_discussions",
        "summary_incremental_update_count",
        server_default=None,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("news_item_discussions", "summary_incremental_update_count")
    op.drop_column("news_item_discussions", "summary_seen_comment_fingerprints")
    op.drop_column("news_item_discussions", "summary_seen_comment_count")
    op.drop_column("news_item_discussions", "summary_seen_input_sha256")
    op.drop_column("news_item_discussions", "summary_comment_fingerprints")
    op.drop_column("news_item_discussions", "summary_comment_count")
    op.drop_column("news_item_discussions", "summary_input_sha256")
