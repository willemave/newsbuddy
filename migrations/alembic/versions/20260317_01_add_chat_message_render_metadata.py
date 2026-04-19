"""Add structured render metadata to chat messages.

Revision ID: 20260317_01
Revises: 20260308_02
Create Date: 2026-03-17
"""

import sqlalchemy as sa
from alembic import op

revision = "20260317_01"
down_revision = "20260308_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("render_metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "render_metadata")
