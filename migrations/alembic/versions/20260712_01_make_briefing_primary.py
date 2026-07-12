"""Make Briefing the primary reading experience for every user.

Revision ID: 20260712_01
Revises: 20260711_02
Create Date: 2026-07-12 21:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_01"
down_revision: str | None = "20260711_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE users SET reading_experience = 'briefing'")
    op.alter_column(
        "users",
        "reading_experience",
        existing_type=sa.String(length=16),
        existing_nullable=False,
        server_default="briefing",
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "reading_experience",
        existing_type=sa.String(length=16),
        existing_nullable=False,
        server_default=None,
    )
