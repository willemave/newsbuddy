"""Add chat session context snapshot.

Revision ID: 20260308_01
Revises: 20260301_01
Create Date: 2026-03-08
"""

import sqlalchemy as sa
from alembic import op

revision = "20260308_01"
down_revision = "20260301_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("context_snapshot", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_sessions", "context_snapshot")
