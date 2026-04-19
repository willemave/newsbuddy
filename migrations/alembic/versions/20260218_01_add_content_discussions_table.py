"""Add content_discussions table for discussion/comment payloads."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260218_01"
down_revision: str | None = "20260215_03"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create content_discussions table and indexes."""
    op.create_table(
        "content_discussions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("discussion_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_id", name="uq_content_discussions_content"),
    )

    op.create_index(
        "idx_content_discussions_platform",
        "content_discussions",
        ["platform"],
        unique=False,
    )
    op.create_index(
        "idx_content_discussions_status",
        "content_discussions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "idx_content_discussions_fetched_at",
        "content_discussions",
        ["fetched_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop content_discussions table and indexes."""
    op.drop_index("idx_content_discussions_fetched_at", table_name="content_discussions")
    op.drop_index("idx_content_discussions_status", table_name="content_discussions")
    op.drop_index("idx_content_discussions_platform", table_name="content_discussions")
    op.drop_table("content_discussions")
