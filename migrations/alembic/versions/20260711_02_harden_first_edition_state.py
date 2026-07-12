"""Remove duplicated first-edition state and scope the active-run index.

Revision ID: 20260711_02
Revises: 20260711_01
Create Date: 2026-07-11 19:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260711_02"
down_revision: str | None = "20260711_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "uq_onboarding_first_edition_active_user",
        table_name="onboarding_first_edition_runs",
    )
    op.create_index(
        "uq_onboarding_first_edition_active_user",
        "onboarding_first_edition_runs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_column("onboarding_first_edition_sources", "completion_sequence")
    op.drop_column("onboarding_first_edition_runs", "ready_at")
    op.drop_column("onboarding_first_edition_runs", "ready_category_keys")
    op.drop_column("onboarding_first_edition_runs", "connected_source_count")


def downgrade() -> None:
    op.add_column(
        "onboarding_first_edition_runs",
        sa.Column(
            "connected_source_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.alter_column(
        "onboarding_first_edition_runs",
        "connected_source_count",
        server_default=None,
    )
    op.add_column(
        "onboarding_first_edition_runs",
        sa.Column(
            "ready_category_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column(
        "onboarding_first_edition_runs",
        "ready_category_keys",
        server_default=None,
    )
    op.add_column(
        "onboarding_first_edition_runs",
        sa.Column("ready_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "onboarding_first_edition_sources",
        sa.Column("completion_sequence", sa.Integer(), nullable=True),
    )
    op.drop_index(
        "uq_onboarding_first_edition_active_user",
        table_name="onboarding_first_edition_runs",
    )
    op.create_index(
        "uq_onboarding_first_edition_active_user",
        "onboarding_first_edition_runs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'ready')"),
    )
