"""Widen news item summary_title to text.

Revision ID: 20260408_01
Revises: 20260404_01
Create Date: 2026-04-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "20260408_01"
down_revision: str | None = "20260404_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "news_items",
        "summary_title",
        existing_type=sa.String(length=240),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "news_items",
        "summary_title",
        existing_type=sa.Text(),
        type_=sa.String(length=240),
        existing_nullable=True,
    )
