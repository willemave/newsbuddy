"""Add feed discovery run + suggestion tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260110_01"
down_revision = "20260103_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feed_discovery_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("direction_summary", sa.Text(), nullable=True),
        sa.Column(
            "seed_content_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("random_seed", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_feed_discovery_runs_user_created",
        "feed_discovery_runs",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feed_discovery_runs_user_id"),
        "feed_discovery_runs",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feed_discovery_runs_status"),
        "feed_discovery_runs",
        ["status"],
        unique=False,
    )

    op.create_table(
        "feed_discovery_suggestions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("suggestion_type", sa.String(length=50), nullable=False),
        sa.Column("site_url", sa.String(length=2048), nullable=True),
        sa.Column("feed_url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("channel_id", sa.String(length=255), nullable=True),
        sa.Column("playlist_id", sa.String(length=255), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="new"),
        sa.Column(
            "config",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "feed_url", name="uq_feed_discovery_user_feed"),
    )
    op.create_index(
        "idx_feed_discovery_suggestions_user_status",
        "feed_discovery_suggestions",
        ["user_id", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feed_discovery_suggestions_run_id"),
        "feed_discovery_suggestions",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feed_discovery_suggestions_user_id"),
        "feed_discovery_suggestions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feed_discovery_suggestions_suggestion_type"),
        "feed_discovery_suggestions",
        ["suggestion_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feed_discovery_suggestions_status"),
        "feed_discovery_suggestions",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_feed_discovery_suggestions_status"), table_name="feed_discovery_suggestions"
    )
    op.drop_index(
        op.f("ix_feed_discovery_suggestions_suggestion_type"),
        table_name="feed_discovery_suggestions",
    )
    op.drop_index(
        op.f("ix_feed_discovery_suggestions_user_id"), table_name="feed_discovery_suggestions"
    )
    op.drop_index(
        op.f("ix_feed_discovery_suggestions_run_id"), table_name="feed_discovery_suggestions"
    )
    op.drop_index(
        "idx_feed_discovery_suggestions_user_status",
        table_name="feed_discovery_suggestions",
    )
    op.drop_table("feed_discovery_suggestions")

    op.drop_index(op.f("ix_feed_discovery_runs_status"), table_name="feed_discovery_runs")
    op.drop_index(op.f("ix_feed_discovery_runs_user_id"), table_name="feed_discovery_runs")
    op.drop_index("idx_feed_discovery_runs_user_created", table_name="feed_discovery_runs")
    op.drop_table("feed_discovery_runs")
