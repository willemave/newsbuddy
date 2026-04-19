"""Add content_favorites table

Revision ID: add_content_favorites
Revises: 5755b73ec0da
Create Date: 2025-08-09 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_content_favorites"
down_revision: str | None = "5755b73ec0da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create content_favorites table
    op.create_table(
        "content_favorites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column("favorited_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_content_favorites_session_content",
        "content_favorites",
        ["session_id", "content_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_content_favorites_content_id"), "content_favorites", ["content_id"], unique=False
    )
    op.create_index(
        op.f("ix_content_favorites_session_id"), "content_favorites", ["session_id"], unique=False
    )


def downgrade() -> None:
    # Drop content_favorites table
    op.drop_index(op.f("ix_content_favorites_session_id"), table_name="content_favorites")
    op.drop_index(op.f("ix_content_favorites_content_id"), table_name="content_favorites")
    op.drop_index("idx_content_favorites_session_content", table_name="content_favorites")
    op.drop_table("content_favorites")
