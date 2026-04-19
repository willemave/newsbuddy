"""Add item_url to feed discovery suggestions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260110_02"
down_revision = "20260110_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "feed_discovery_suggestions",
        sa.Column("item_url", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("feed_discovery_suggestions", "item_url")
