"""add tutorial completion flag

Revision ID: 83418b46cd01
Revises: 20260114_02
Create Date: 2026-01-16 22:20:37.886496

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "83418b46cd01"
down_revision: str | None = "20260114_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column(
            "has_completed_new_user_tutorial",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "has_completed_new_user_tutorial")
