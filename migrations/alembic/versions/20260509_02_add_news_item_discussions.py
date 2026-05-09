"""add news item discussions

Revision ID: 20260509_02
Revises: 20260509_01
Create Date: 2026-05-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260509_02"
down_revision: str | None = "20260509_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "news_item_discussions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("news_item_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("discussion_url", sa.String(length=2048), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("comment_count", sa.Integer(), nullable=True),
        sa.Column("raw_comments_ref", sa.JSON(), nullable=True),
        sa.Column("raw_comments_sha256", sa.String(length=64), nullable=True),
        sa.Column("fetched_comment_count", sa.Integer(), nullable=True),
        sa.Column("last_count_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_comments_fetched_at", sa.DateTime(), nullable=True),
        sa.Column("next_refresh_after", sa.DateTime(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column(
            "summary_status",
            sa.String(length=20),
            nullable=False,
            server_default="not_ready",
        ),
        sa.Column("summary_version", sa.Integer(), nullable=True),
        sa.Column("summary_model", sa.String(length=100), nullable=True),
        sa.Column("summary_generated_at", sa.DateTime(), nullable=True),
        sa.Column(
            "last_refresh_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("last_refresh_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("news_item_id", name="uq_news_item_discussions_news_item"),
    )
    op.create_index(
        "idx_news_item_discussions_platform_external",
        "news_item_discussions",
        ["platform", "external_id"],
        unique=False,
    )
    op.create_index(
        "idx_news_item_discussions_next_refresh",
        "news_item_discussions",
        ["next_refresh_after"],
        unique=False,
    )
    op.create_index(
        "idx_news_item_discussions_status",
        "news_item_discussions",
        ["last_refresh_status"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_news_item_discussions_status", table_name="news_item_discussions")
    op.drop_index("idx_news_item_discussions_next_refresh", table_name="news_item_discussions")
    op.drop_index("idx_news_item_discussions_platform_external", table_name="news_item_discussions")
    op.drop_table("news_item_discussions")
