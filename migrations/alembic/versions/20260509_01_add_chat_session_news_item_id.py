"""add chat session news item id

Revision ID: 20260509_01
Revises: 20260507_02
Create Date: 2026-05-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260509_01"
down_revision: str | None = "20260507_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("chat_sessions", sa.Column("news_item_id", sa.Integer(), nullable=True))
    op.create_index("ix_chat_sessions_news_item_id", "chat_sessions", ["news_item_id"])
    op.create_index(
        "idx_chat_sessions_news_item",
        "chat_sessions",
        ["user_id", "news_item_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_chat_sessions_news_item", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_news_item_id", table_name="chat_sessions")
    op.drop_column("chat_sessions", "news_item_id")
