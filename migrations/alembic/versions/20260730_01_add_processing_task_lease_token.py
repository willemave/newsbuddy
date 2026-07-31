"""Add exact claim identity to processing task leases.

Revision ID: 20260730_01
Revises: 20260725_01
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_01"
down_revision: str | None = "20260725_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add a nullable UUID populated by each new claim attempt."""
    op.add_column(
        "processing_tasks",
        sa.Column("lease_token", sa.Uuid(), nullable=True),
    )


def downgrade() -> None:
    """Remove exact claim identity from processing tasks."""
    op.drop_column("processing_tasks", "lease_token")
