"""Add token usage fields to feed discovery runs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260114_01"
down_revision = "20260112_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("feed_discovery_runs") as batch_op:
        batch_op.add_column(sa.Column("token_input", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("token_output", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("token_total", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("token_usage", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("feed_discovery_runs") as batch_op:
        batch_op.drop_column("token_usage")
        batch_op.drop_column("token_total")
        batch_op.drop_column("token_output")
        batch_op.drop_column("token_input")
