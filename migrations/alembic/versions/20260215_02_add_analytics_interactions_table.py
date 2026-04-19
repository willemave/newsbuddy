"""Add analytics_interactions table for user content interaction analytics."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260215_02"
down_revision: str | None = "20260208_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create analytics_interactions table and supporting indexes."""
    op.create_table(
        "analytics_interactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column("interaction_type", sa.String(length=32), nullable=False),
        sa.Column("interaction_id", sa.String(length=36), nullable=False),
        sa.Column("surface", sa.String(length=64), nullable=True),
        sa.Column("context_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "occurred_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "interaction_id",
            name="uq_analytics_interactions_user_interaction",
        ),
    )

    op.create_index(
        "ix_analytics_interactions_user_id",
        "analytics_interactions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_analytics_interactions_content_id",
        "analytics_interactions",
        ["content_id"],
        unique=False,
    )
    op.create_index(
        "ix_analytics_interactions_interaction_type",
        "analytics_interactions",
        ["interaction_type"],
        unique=False,
    )
    op.create_index(
        "ix_analytics_interactions_occurred_at",
        "analytics_interactions",
        ["occurred_at"],
        unique=False,
    )
    op.create_index(
        "idx_analytics_interactions_user_type_occurred",
        "analytics_interactions",
        ["user_id", "interaction_type", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "idx_analytics_interactions_user_content_occurred",
        "analytics_interactions",
        ["user_id", "content_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop analytics_interactions table and indexes."""
    op.drop_index(
        "idx_analytics_interactions_user_content_occurred",
        table_name="analytics_interactions",
    )
    op.drop_index(
        "idx_analytics_interactions_user_type_occurred",
        table_name="analytics_interactions",
    )
    op.drop_index("ix_analytics_interactions_occurred_at", table_name="analytics_interactions")
    op.drop_index(
        "ix_analytics_interactions_interaction_type",
        table_name="analytics_interactions",
    )
    op.drop_index("ix_analytics_interactions_content_id", table_name="analytics_interactions")
    op.drop_index("ix_analytics_interactions_user_id", table_name="analytics_interactions")
    op.drop_table("analytics_interactions")
