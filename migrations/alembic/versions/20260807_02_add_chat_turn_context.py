"""Persist immutable queued chat-turn context.

Revision ID: 20260807_02
Revises: 20260807_01
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_02"
down_revision: str | None = "20260807_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add immutable inputs and close unrecoverable legacy processing turns."""
    op.add_column(
        "chat_messages",
        sa.Column("processing_context", sa.JSON(), nullable=True),
    )
    op.execute(
        """
        UPDATE chat_messages
        SET status = 'failed',
            error = 'This message was interrupted by an app update. Please retry.'
        WHERE status = 'processing'
        """
    )


def downgrade() -> None:
    """Remove immutable queued-turn context."""
    op.drop_column("chat_messages", "processing_context")
