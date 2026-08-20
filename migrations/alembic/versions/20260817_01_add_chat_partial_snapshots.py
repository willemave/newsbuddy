"""Add durable advisory snapshots for in-flight chat responses.

Revision ID: 20260817_01
Revises: 20260815_01
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_01"
down_revision: str | None = "20260815_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add retry-fenced cumulative partial response fields."""
    op.add_column("chat_messages", sa.Column("partial_text", sa.Text(), nullable=True))
    op.add_column("chat_messages", sa.Column("stream_generation", sa.Integer(), nullable=True))
    op.add_column("chat_messages", sa.Column("stream_revision", sa.Integer(), nullable=True))
    op.add_column(
        "chat_messages",
        sa.Column("stream_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Remove partial response fields."""
    op.drop_column("chat_messages", "stream_updated_at")
    op.drop_column("chat_messages", "stream_revision")
    op.drop_column("chat_messages", "stream_generation")
    op.drop_column("chat_messages", "partial_text")
