"""add briefing tables

Revision ID: 20260702_01
Revises: 20260619_01
Create Date: 2026-07-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260702_01"
down_revision: str | None = "20260619_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "briefing_lenses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=220), nullable=False),
        sa.Column("deck", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("centroid", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key", name="uq_briefing_lenses_user_key"),
    )
    op.create_index("ix_briefing_lenses_user_id", "briefing_lenses", ["user_id"])
    op.create_index("ix_briefing_lenses_tier", "briefing_lenses", ["tier"])
    op.create_index("ix_briefing_lenses_status", "briefing_lenses", ["status"])
    op.create_index(
        "idx_briefing_lenses_user_status_position",
        "briefing_lenses",
        ["user_id", "status", "position"],
    )

    op.create_table(
        "briefing_segments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lens_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("blocks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("markdown_raw", sa.Text(), nullable=False),
        sa.Column("narration_text", sa.Text(), nullable=False),
        sa.Column("source_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=16), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("generation_ms", sa.Integer(), nullable=True),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_briefing_segments_lens_id", "briefing_segments", ["lens_id"])
    op.create_index("ix_briefing_segments_user_id", "briefing_segments", ["user_id"])
    op.create_index("ix_briefing_segments_status", "briefing_segments", ["status"])
    op.create_index("ix_briefing_segments_created_at", "briefing_segments", ["created_at"])
    op.create_index(
        "idx_briefing_segments_user_status_created",
        "briefing_segments",
        ["user_id", "status", "created_at"],
    )
    op.create_index(
        "idx_briefing_segments_lens_status_created",
        "briefing_segments",
        ["lens_id", "status", "created_at"],
    )

    op.create_table(
        "briefing_pending_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("lens_key", sa.String(length=64), nullable=True),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column(
            "enqueued_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "source_kind",
            "source_id",
            name="uq_briefing_pending_sources_user_source",
        ),
    )
    op.create_index("ix_briefing_pending_sources_user_id", "briefing_pending_sources", ["user_id"])
    op.create_index(
        "ix_briefing_pending_sources_lens_key", "briefing_pending_sources", ["lens_key"]
    )
    op.create_index(
        "idx_briefing_pending_sources_user_lens",
        "briefing_pending_sources",
        ["user_id", "lens_key", "enqueued_at"],
    )

    op.create_table(
        "briefing_states",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("masthead_title", sa.String(length=220), nullable=False),
        sa.Column("masthead_deck", sa.Text(), nullable=False),
        sa.Column("last_append_at", sa.DateTime(), nullable=True),
        sa.Column("last_sweep_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("briefing_states")
    op.drop_index("idx_briefing_pending_sources_user_lens", table_name="briefing_pending_sources")
    op.drop_index("ix_briefing_pending_sources_lens_key", table_name="briefing_pending_sources")
    op.drop_index("ix_briefing_pending_sources_user_id", table_name="briefing_pending_sources")
    op.drop_table("briefing_pending_sources")
    op.drop_index("idx_briefing_segments_lens_status_created", table_name="briefing_segments")
    op.drop_index("idx_briefing_segments_user_status_created", table_name="briefing_segments")
    op.drop_index("ix_briefing_segments_created_at", table_name="briefing_segments")
    op.drop_index("ix_briefing_segments_status", table_name="briefing_segments")
    op.drop_index("ix_briefing_segments_user_id", table_name="briefing_segments")
    op.drop_index("ix_briefing_segments_lens_id", table_name="briefing_segments")
    op.drop_table("briefing_segments")
    op.drop_index("idx_briefing_lenses_user_status_position", table_name="briefing_lenses")
    op.drop_index("ix_briefing_lenses_status", table_name="briefing_lenses")
    op.drop_index("ix_briefing_lenses_tier", table_name="briefing_lenses")
    op.drop_index("ix_briefing_lenses_user_id", table_name="briefing_lenses")
    op.drop_table("briefing_lenses")
