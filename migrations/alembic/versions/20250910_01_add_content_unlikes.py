"""Add content_unlikes table

Revision ID: 20250910_01
Revises:
Create Date: 2025-09-10
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20250910_01"
down_revision = "add_content_favorites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_unlikes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(length=255), nullable=False, index=True),
        sa.Column("content_id", sa.Integer(), nullable=False, index=True),
        sa.Column("unliked_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "idx_content_unlikes_session_content",
        "content_unlikes",
        ["session_id", "content_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_content_unlikes_session_content", table_name="content_unlikes")
    op.drop_table("content_unlikes")
