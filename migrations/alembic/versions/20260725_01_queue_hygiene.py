"""Tighten processing-task dequeue columns and indexes.

Revision ID: 20260725_01
Revises: 20260719_02
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_01"
down_revision: str | None = "20260719_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_RETRY_COUNT_CHECK = "ck_processing_tasks_retry_count_not_null_migration"


def _replace_index_concurrently(name: str, columns: list[str]) -> None:
    """Build a replacement before briefly swapping index names."""
    replacement_name = f"{name}_replacement"
    with op.get_context().autocommit_block():
        op.execute(sa.text(f'DROP INDEX CONCURRENTLY IF EXISTS "{replacement_name}"'))
        op.create_index(
            replacement_name,
            "processing_tasks",
            columns,
            unique=False,
            postgresql_concurrently=True,
        )
        op.drop_index(
            name,
            table_name="processing_tasks",
            if_exists=True,
            postgresql_concurrently=True,
        )
        op.execute(sa.text(f'ALTER INDEX "{replacement_name}" RENAME TO "{name}"'))


def upgrade() -> None:
    """Make retry buckets non-null and align indexes with dequeue ordering."""
    op.execute(sa.text("UPDATE processing_tasks SET retry_count = 0 WHERE retry_count IS NULL"))
    op.create_check_constraint(
        _RETRY_COUNT_CHECK,
        "processing_tasks",
        "retry_count IS NOT NULL",
        postgresql_not_valid=True,
    )
    op.execute(sa.text(f'ALTER TABLE processing_tasks VALIDATE CONSTRAINT "{_RETRY_COUNT_CHECK}"'))
    op.alter_column(
        "processing_tasks",
        "retry_count",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
        existing_server_default=sa.text("0"),
    )
    op.drop_constraint(_RETRY_COUNT_CHECK, "processing_tasks", type_="check")

    _replace_index_concurrently(
        "idx_task_status_available",
        ["status", "retry_count", "available_at", "created_at", "id"],
    )
    _replace_index_concurrently(
        "idx_task_queue_status_available",
        ["queue_name", "status", "retry_count", "available_at", "created_at", "id"],
    )


def downgrade() -> None:
    """Restore the previous dequeue indexes and retry-count nullability."""
    _replace_index_concurrently(
        "idx_task_status_available",
        ["status", "available_at", "retry_count", "created_at"],
    )
    _replace_index_concurrently(
        "idx_task_queue_status_available",
        ["queue_name", "status", "available_at", "retry_count", "created_at"],
    )
    op.alter_column(
        "processing_tasks",
        "retry_count",
        existing_type=sa.Integer(),
        nullable=True,
        server_default=sa.text("0"),
        existing_server_default=sa.text("0"),
    )
