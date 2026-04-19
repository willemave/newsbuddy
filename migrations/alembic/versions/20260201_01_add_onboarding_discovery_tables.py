"""add onboarding discovery tables

Revision ID: 20260201_01
Revises: 83418b46cd01
Create Date: 2026-02-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260201_01"
down_revision: str | None = "83418b46cd01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "onboarding_discovery_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("topic_summary", sa.Text(), nullable=True),
        sa.Column("inferred_topics", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("lane_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_onboarding_discovery_runs_user_created",
        "onboarding_discovery_runs",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_onboarding_discovery_runs_status",
        "onboarding_discovery_runs",
        ["status"],
    )
    op.create_index(
        "ix_onboarding_discovery_runs_user_id",
        "onboarding_discovery_runs",
        ["user_id"],
    )

    op.create_table(
        "onboarding_discovery_lanes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("lane_name", sa.String(length=160), nullable=False),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("target", sa.String(length=30), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("query_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_queries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queries", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_onboarding_discovery_lanes_run",
        "onboarding_discovery_lanes",
        ["run_id"],
    )
    op.create_index(
        "ix_onboarding_discovery_lanes_status",
        "onboarding_discovery_lanes",
        ["status"],
    )

    op.create_table(
        "onboarding_discovery_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("suggestion_type", sa.String(length=50), nullable=False),
        sa.Column("site_url", sa.String(length=2048), nullable=True),
        sa.Column("feed_url", sa.String(length=2048), nullable=True),
        sa.Column("subreddit", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_onboarding_discovery_suggestions_run",
        "onboarding_discovery_suggestions",
        ["run_id"],
    )
    op.create_index(
        "idx_onboarding_discovery_suggestions_user_status",
        "onboarding_discovery_suggestions",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_onboarding_discovery_suggestions_suggestion_type",
        "onboarding_discovery_suggestions",
        ["suggestion_type"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_onboarding_discovery_suggestions_suggestion_type",
        table_name="onboarding_discovery_suggestions",
    )
    op.drop_index(
        "idx_onboarding_discovery_suggestions_user_status",
        table_name="onboarding_discovery_suggestions",
    )
    op.drop_index(
        "idx_onboarding_discovery_suggestions_run", table_name="onboarding_discovery_suggestions"
    )
    op.drop_table("onboarding_discovery_suggestions")

    op.drop_index("ix_onboarding_discovery_lanes_status", table_name="onboarding_discovery_lanes")
    op.drop_index("idx_onboarding_discovery_lanes_run", table_name="onboarding_discovery_lanes")
    op.drop_table("onboarding_discovery_lanes")

    op.drop_index("ix_onboarding_discovery_runs_user_id", table_name="onboarding_discovery_runs")
    op.drop_index("ix_onboarding_discovery_runs_status", table_name="onboarding_discovery_runs")
    op.drop_index(
        "idx_onboarding_discovery_runs_user_created", table_name="onboarding_discovery_runs"
    )
    op.drop_table("onboarding_discovery_runs")
