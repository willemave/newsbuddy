"""Persist Briefing segment event groups.

Revision ID: 20260822_01
Revises: 20260820_01
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260822_01"
down_revision: str | None = "20260820_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Store how a segment's source keys group into events; null means one event per source."""
    op.add_column(
        "briefing_segments",
        sa.Column("event_groups", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Drop persisted event groups."""
    op.drop_column("briefing_segments", "event_groups")
