"""add_chat_message_status_columns

Revision ID: cdcc53c1ac56
Revises: 1620a8545d8c
Create Date: 2025-12-03 22:36:22.425243

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cdcc53c1ac56"
down_revision: str | None = "1620a8545d8c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add status column with default 'completed' for existing messages
    op.add_column(
        "chat_messages",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="completed",
        ),
    )
    op.create_index("ix_chat_messages_status", "chat_messages", ["status"])

    # Add error column for failed messages
    op.add_column(
        "chat_messages",
        sa.Column("error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_chat_messages_status", "chat_messages")
    op.drop_column("chat_messages", "error")
    op.drop_column("chat_messages", "status")
