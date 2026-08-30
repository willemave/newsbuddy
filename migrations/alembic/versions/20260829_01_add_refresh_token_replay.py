"""Add bounded replay retrieval to refresh-token rotation.

Revision ID: 20260829_01
Revises: 20260825_01
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_01"
down_revision: str | None = "20260825_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store one encrypted, expiring response for an optional rotation attempt."""
    op.add_column(
        "consumed_refresh_tokens",
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "consumed_refresh_tokens",
        sa.Column("replay_payload_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "consumed_refresh_tokens",
        sa.Column("replay_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_consumed_refresh_tokens_replay_expiry",
        "consumed_refresh_tokens",
        ["replay_expires_at"],
    )


def downgrade() -> None:
    """Remove refresh replay material while preserving one-time consumption."""
    op.drop_index(
        "idx_consumed_refresh_tokens_replay_expiry",
        table_name="consumed_refresh_tokens",
    )
    op.drop_column("consumed_refresh_tokens", "replay_expires_at")
    op.drop_column("consumed_refresh_tokens", "replay_payload_encrypted")
    op.drop_column("consumed_refresh_tokens", "attempt_id")
