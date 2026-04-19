"""Add digest interval settings and checkpoint coverage tracking.

Revision ID: 20260319_02
Revises: 20260319_01
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260319_02"
down_revision = "20260319_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "news_digest_interval_hours",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("6"),
        ),
    )
    op.add_column(
        "daily_news_digests",
        sa.Column("coverage_end_at", sa.DateTime(), nullable=True),
    )

    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.alter_column("users", "news_digest_interval_hours", server_default=None)


def downgrade() -> None:
    op.drop_column("daily_news_digests", "coverage_end_at")
    op.drop_column("users", "news_digest_interval_hours")
