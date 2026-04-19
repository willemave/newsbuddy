"""Add queue partitioning for processing tasks."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260208_01"
down_revision: str | None = "20260204_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add queue_name and backfill existing tasks into queue partitions."""
    op.add_column(
        "processing_tasks",
        sa.Column(
            "queue_name",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'content'"),
        ),
    )

    op.execute(
        sa.text(
            "UPDATE processing_tasks SET queue_name = 'onboarding' "
            "WHERE task_type = 'onboarding_discover'"
        )
    )
    op.execute(
        sa.text("UPDATE processing_tasks SET queue_name = 'chat' WHERE task_type = 'dig_deeper'")
    )

    op.create_index(
        "idx_task_queue_status_created",
        "processing_tasks",
        ["queue_name", "status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove queue partitioning from processing tasks."""
    op.drop_index("idx_task_queue_status_created", table_name="processing_tasks")
    op.drop_column("processing_tasks", "queue_name")
