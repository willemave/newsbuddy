"""Add timing fields to feed discovery runs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260114_02"
down_revision = "20260114_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("feed_discovery_runs") as batch_op:
        batch_op.add_column(sa.Column("duration_ms_total", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("duration_ms_direction", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("duration_ms_lane", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("duration_ms_candidate_extract", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("duration_ms_candidate_validate", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("duration_ms_persist", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("timing", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("feed_discovery_runs") as batch_op:
        batch_op.drop_column("timing")
        batch_op.drop_column("duration_ms_persist")
        batch_op.drop_column("duration_ms_candidate_validate")
        batch_op.drop_column("duration_ms_candidate_extract")
        batch_op.drop_column("duration_ms_lane")
        batch_op.drop_column("duration_ms_direction")
        batch_op.drop_column("duration_ms_total")
