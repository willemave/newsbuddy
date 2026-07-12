"""Add first-edition onboarding progress and reading experience.

Revision ID: 20260711_01
Revises: 20260706_01
Create Date: 2026-07-11 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260711_01"
down_revision: str | None = "20260706_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "reading_experience", sa.String(length=16), nullable=False, server_default="classic"
        ),
    )
    op.alter_column("users", "reading_experience", server_default=None)

    op.create_table(
        "onboarding_first_edition_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("connected_source_count", sa.Integer(), nullable=False),
        sa.Column("ready_category_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ready_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_onboarding_first_edition_active_user",
        "onboarding_first_edition_runs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'ready')"),
    )
    op.create_index(
        "ix_onboarding_first_edition_runs_user_id",
        "onboarding_first_edition_runs",
        ["user_id"],
    )
    op.create_index(
        "ix_onboarding_first_edition_runs_status",
        "onboarding_first_edition_runs",
        ["status"],
    )

    op.create_table(
        "onboarding_first_edition_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("source_key", sa.String(length=160), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("completion_sequence", sa.Integer(), nullable=True),
        sa.Column("processed_item_count", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "source_key",
            name="uq_onboarding_first_edition_sources_run_source",
        ),
    )
    op.create_index(
        "ix_onboarding_first_edition_sources_run_id",
        "onboarding_first_edition_sources",
        ["run_id"],
    )
    op.create_index(
        "ix_onboarding_first_edition_sources_status",
        "onboarding_first_edition_sources",
        ["status"],
    )
    op.create_index(
        "idx_onboarding_first_edition_sources_run_position",
        "onboarding_first_edition_sources",
        ["run_id", "position"],
    )


def downgrade() -> None:
    op.drop_table("onboarding_first_edition_sources")
    op.drop_table("onboarding_first_edition_runs")
    op.drop_column("users", "reading_experience")
