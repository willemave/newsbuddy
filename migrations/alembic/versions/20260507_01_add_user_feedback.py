"""add user feedback table

Revision ID: 20260507_01
Revises: 20260419_01
Create Date: 2026-05-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260507_01"
down_revision: str | None = "20260419_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "source",
            sa.String(length=64),
            nullable=False,
            server_default="ios_settings",
        ),
        sa.Column("app_version", sa.String(length=64), nullable=True),
        sa.Column("build_number", sa.String(length=64), nullable=True),
        sa.Column("platform", sa.String(length=64), nullable=True),
        sa.Column("os_version", sa.String(length=128), nullable=True),
        sa.Column("device_model", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_feedback_user_id"), "user_feedback", ["user_id"])
    op.create_index(op.f("ix_user_feedback_created_at"), "user_feedback", ["created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_user_feedback_created_at"), table_name="user_feedback")
    op.drop_index(op.f("ix_user_feedback_user_id"), table_name="user_feedback")
    op.drop_table("user_feedback")
