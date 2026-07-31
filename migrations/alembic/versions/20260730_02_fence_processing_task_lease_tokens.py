"""Fence processing-task lease tokens at the database boundary.

Revision ID: 20260730_02
Revises: 20260730_01
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260730_02"
down_revision: str | None = "20260730_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

CONSTRAINT_NAME = "ck_processing_tasks_lease_token_has_owner"


def upgrade() -> None:
    """Reject tokenized rows that no longer represent an owned claim."""
    op.execute(
        """
        UPDATE processing_tasks
        SET status = CASE
              WHEN status IS NULL OR status = 'processing' THEN 'pending'
              ELSE status
            END,
            available_at = CASE
              WHEN status IS NULL OR status = 'processing'
                THEN timezone('UTC', NOW())
              ELSE available_at
            END,
            started_at = CASE
              WHEN status IS NULL OR status IN ('pending', 'processing') THEN NULL
              ELSE started_at
            END,
            completed_at = CASE
              WHEN status IS NULL OR status IN ('pending', 'processing') THEN NULL
              ELSE completed_at
            END,
            locked_at = NULL,
            locked_by = NULL,
            lease_token = NULL,
            lease_expires_at = NULL
        WHERE lease_token IS NOT NULL
          AND (
            status IS DISTINCT FROM 'processing'
            OR locked_at IS NULL
            OR locked_by IS NULL
            OR lease_expires_at IS NULL
          )
        """
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "processing_tasks",
        "lease_token IS NULL OR "
        "(status IS NOT NULL AND status = 'processing' "
        "AND locked_at IS NOT NULL AND locked_by IS NOT NULL "
        "AND lease_expires_at IS NOT NULL)",
    )


def downgrade() -> None:
    """Remove the cross-version lease-token fence."""
    op.drop_constraint(CONSTRAINT_NAME, "processing_tasks", type_="check")
