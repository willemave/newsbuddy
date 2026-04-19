"""Add processing task leasing and dedupe fields.

Revision ID: 20260409_01
Revises: 20260408_01
Create Date: 2026-04-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260409_01"
down_revision: str | None = "20260408_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add queue leasing columns and indexes."""
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.add_column(
        "processing_tasks",
        sa.Column(
            "available_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column("processing_tasks", sa.Column("locked_at", sa.DateTime(), nullable=True))
    op.add_column(
        "processing_tasks",
        sa.Column("locked_by", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "processing_tasks",
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "processing_tasks",
        sa.Column("dedupe_key", sa.String(length=512), nullable=True),
    )

    op.execute(
        sa.text(
            "UPDATE processing_tasks SET available_at = COALESCE(created_at, CURRENT_TIMESTAMP)"
        )
    )
    if is_postgres:
        op.execute(
            sa.text(
                "UPDATE processing_tasks "
                "SET dedupe_key = queue_name || '|' || task_type || '|content:' || content_id::text "
                "WHERE dedupe_key IS NULL "
                "AND content_id IS NOT NULL "
                "AND task_type IN ("
                "'process_content', 'process_podcast_media', 'summarize', "
                "'fetch_discussion', 'generate_image'"
                ")"
            )
        )

    op.create_index(
        "idx_task_status_available",
        "processing_tasks",
        ["status", "available_at", "retry_count", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_task_queue_status_available",
        "processing_tasks",
        ["queue_name", "status", "available_at", "retry_count", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_task_status_lease_expires",
        "processing_tasks",
        ["status", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_processing_tasks_locked_by",
        "processing_tasks",
        ["locked_by"],
        unique=False,
    )
    op.create_index(
        "ix_processing_tasks_lease_expires_at",
        "processing_tasks",
        ["lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_processing_tasks_available_at",
        "processing_tasks",
        ["available_at"],
        unique=False,
    )
    op.create_index(
        "uq_processing_tasks_dedupe_key_active",
        "processing_tasks",
        ["dedupe_key"],
        unique=is_postgres,
        postgresql_where=sa.text("dedupe_key IS NOT NULL AND status IN ('pending', 'processing')"),
    )


def downgrade() -> None:
    """Remove queue leasing columns and indexes."""
    op.drop_index("uq_processing_tasks_dedupe_key_active", table_name="processing_tasks")
    op.drop_index("ix_processing_tasks_available_at", table_name="processing_tasks")
    op.drop_index("ix_processing_tasks_lease_expires_at", table_name="processing_tasks")
    op.drop_index("ix_processing_tasks_locked_by", table_name="processing_tasks")
    op.drop_index("idx_task_status_lease_expires", table_name="processing_tasks")
    op.drop_index("idx_task_queue_status_available", table_name="processing_tasks")
    op.drop_index("idx_task_status_available", table_name="processing_tasks")
    op.drop_column("processing_tasks", "dedupe_key")
    op.drop_column("processing_tasks", "lease_expires_at")
    op.drop_column("processing_tasks", "locked_by")
    op.drop_column("processing_tasks", "locked_at")
    op.drop_column("processing_tasks", "available_at")
