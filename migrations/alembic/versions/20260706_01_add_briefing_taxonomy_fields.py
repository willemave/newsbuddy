"""add briefing taxonomy fields

Revision ID: 20260706_01
Revises: 20260702_01
Create Date: 2026-07-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260706_01"
down_revision: str | None = "20260702_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "briefing_lenses",
        sa.Column("centroid_weight", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "briefing_lenses",
        sa.Column("centroid_model", sa.String(length=120), nullable=True),
    )
    op.add_column("briefing_lenses", sa.Column("routing_rule", sa.Text(), nullable=True))
    op.alter_column("briefing_lenses", "centroid_weight", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("briefing_lenses", "routing_rule")
    op.drop_column("briefing_lenses", "centroid_model")
    op.drop_column("briefing_lenses", "centroid_weight")
