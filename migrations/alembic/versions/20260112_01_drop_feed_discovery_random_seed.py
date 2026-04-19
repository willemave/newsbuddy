"""Drop random_seed from feed discovery runs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260112_01"
down_revision = "20260110_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("feed_discovery_runs") as batch_op:
        batch_op.drop_column("random_seed")


def downgrade() -> None:
    with op.batch_alter_table("feed_discovery_runs") as batch_op:
        batch_op.add_column(
            sa.Column("random_seed", sa.Integer(), nullable=False, server_default="0")
        )
    op.execute("UPDATE feed_discovery_runs SET random_seed = 0")
    with op.batch_alter_table("feed_discovery_runs") as batch_op:
        batch_op.alter_column("random_seed", server_default=None)
