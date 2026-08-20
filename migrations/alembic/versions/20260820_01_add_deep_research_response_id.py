"""Add resumable Deep Research provider response identity.

Revision ID: 20260820_01
Revises: 20260817_01
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_01"
down_revision: str | None = "20260817_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Store the provider run reused by reclaimed Deep Research workers."""
    op.add_column(
        "chat_messages",
        sa.Column("deep_research_response_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    """Remove the resumable provider run identity."""
    op.drop_column("chat_messages", "deep_research_response_id")
