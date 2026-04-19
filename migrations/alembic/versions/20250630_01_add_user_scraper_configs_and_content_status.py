"""Add user scraper configs and per-user content status."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20250630_01"
down_revision = "20250920_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add user_scraper_configs and content_status tables, then backfill."""
    op.create_table(
        "user_scraper_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("scraper_type", sa.String(length=50), nullable=False, index=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("feed_url", sa.String(length=2048), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "scraper_type", "feed_url", name="uq_user_scraper_feed"),
    )
    op.create_index(
        "idx_user_scraper_user_type", "user_scraper_configs", ["user_id", "scraper_type"]
    )

    op.create_table(
        "content_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("content_id", sa.Integer(), nullable=False, index=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, index=True, server_default="inbox"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "content_id", name="idx_content_status_user_content"),
    )
    _backfill_content_status()


def _backfill_content_status() -> None:
    """Populate content_status for existing article/podcast content per user."""
    conn = op.get_bind()
    now = datetime.utcnow()
    users = [row[0] for row in conn.execute(sa.text("SELECT id FROM users")).fetchall()]
    if not users:
        return

    for user_id in users:
        conn.execute(
            sa.text(
                """
                INSERT INTO content_status (user_id, content_id, status, created_at, updated_at)
                SELECT :user_id, c.id, 'inbox', :now, :now FROM contents c
                WHERE c.content_type IN ('article','podcast')
                  AND (c.classification != 'skip' OR c.classification IS NULL)
                ON CONFLICT(user_id, content_id) DO NOTHING
                """
            ),
            {"user_id": user_id, "now": now},
        )


def downgrade() -> None:
    """Drop new tables."""
    op.drop_table("content_status")
    op.drop_index("idx_user_scraper_user_type", table_name="user_scraper_configs")
    op.drop_table("user_scraper_configs")
