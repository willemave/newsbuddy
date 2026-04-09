"""Add has_completed_onboarding to users.

Revision ID: 20260223_01
Revises: 20260221_01
Create Date: 2026-02-23
"""

import sqlalchemy as sa

from alembic import op

revision = "20260223_01"
down_revision = "20260221_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "has_completed_onboarding",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Backfill: existing users should not see onboarding again
    op.execute("UPDATE users SET has_completed_onboarding = TRUE")


def downgrade() -> None:
    op.drop_column("users", "has_completed_onboarding")
