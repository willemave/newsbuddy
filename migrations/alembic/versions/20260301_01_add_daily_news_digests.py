"""Add daily news digest table and user timezone preference.

Revision ID: 20260301_01
Revises: 20260223_01
Create Date: 2026-03-01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260301_01"
down_revision = "20260223_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "news_digest_timezone",
            sa.String(length=100),
            nullable=False,
            server_default=sa.text("'UTC'"),
        ),
    )

    op.create_table(
        "daily_news_digests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column(
            "timezone", sa.String(length=100), nullable=False, server_default=sa.text("'UTC'")
        ),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("key_points", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("source_content_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("llm_model", sa.String(length=120), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "local_date", name="uq_daily_news_digests_user_date"),
    )

    op.create_index(
        "idx_daily_news_digests_user_local_date",
        "daily_news_digests",
        ["user_id", "local_date"],
        unique=False,
    )
    op.create_index(
        "idx_daily_news_digests_user_read_at",
        "daily_news_digests",
        ["user_id", "read_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_daily_news_digests_local_date"),
        "daily_news_digests",
        ["local_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_daily_news_digests_user_id"),
        "daily_news_digests",
        ["user_id"],
        unique=False,
    )

    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.alter_column("users", "news_digest_timezone", server_default=None)
        op.alter_column("daily_news_digests", "timezone", server_default=None)
        op.alter_column("daily_news_digests", "key_points", server_default=None)
        op.alter_column("daily_news_digests", "source_content_ids", server_default=None)
        op.alter_column("daily_news_digests", "source_count", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_daily_news_digests_user_id"), table_name="daily_news_digests")
    op.drop_index(op.f("ix_daily_news_digests_local_date"), table_name="daily_news_digests")
    op.drop_index("idx_daily_news_digests_user_read_at", table_name="daily_news_digests")
    op.drop_index("idx_daily_news_digests_user_local_date", table_name="daily_news_digests")
    op.drop_table("daily_news_digests")
    op.drop_column("users", "news_digest_timezone")
