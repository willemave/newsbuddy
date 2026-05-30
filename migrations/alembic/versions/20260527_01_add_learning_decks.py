"""add learning decks

Revision ID: 20260527_01
Revises: 20260525_02
Create Date: 2026-05-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260527_01"
down_revision: str | None = "20260525_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "learning_decks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_identity", sa.String(length=512), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("source_content_id", sa.Integer(), nullable=True),
        sa.Column("source_title", sa.String(length=500), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("latest_successful_run_id", sa.Integer(), nullable=True),
        sa.Column("latest_run_id", sa.Integer(), nullable=True),
        sa.Column("artifact_storage_prefix", sa.String(length=2048), nullable=True),
        sa.Column("deck_object_key", sa.String(length=2048), nullable=True),
        sa.Column("source_notes_object_key", sa.String(length=2048), nullable=True),
        sa.Column("source_notes_html_object_key", sa.String(length=2048), nullable=True),
        sa.Column("artifact_object_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("share_enabled", sa.Boolean(), nullable=False),
        sa.Column("share_token_hash", sa.String(length=64), nullable=True),
        sa.Column("share_token_nonce", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_decks_user_id", "learning_decks", ["user_id"])
    op.create_index("ix_learning_decks_source_kind", "learning_decks", ["source_kind"])
    op.create_index(
        "ix_learning_decks_source_content_id",
        "learning_decks",
        ["source_content_id"],
    )
    op.create_index(
        "ix_learning_decks_latest_successful_run_id",
        "learning_decks",
        ["latest_successful_run_id"],
    )
    op.create_index(
        "ix_learning_decks_latest_run_id",
        "learning_decks",
        ["latest_run_id"],
    )
    op.create_index("ix_learning_decks_share_enabled", "learning_decks", ["share_enabled"])
    op.create_index("ix_learning_decks_share_token_hash", "learning_decks", ["share_token_hash"])
    op.create_index("ix_learning_decks_deleted_at", "learning_decks", ["deleted_at"])
    op.create_index(
        "uq_learning_decks_user_source_active",
        "learning_decks",
        ["user_id", "source_identity"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_learning_decks_user_updated",
        "learning_decks",
        ["user_id", "updated_at"],
    )

    op.create_table(
        "learning_deck_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("deck_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("interests_prompt", sa.Text(), nullable=True),
        sa.Column("source_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("timeline", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("artifact_storage_prefix", sa.String(length=2048), nullable=True),
        sa.Column("deck_object_key", sa.String(length=2048), nullable=True),
        sa.Column("source_notes_object_key", sa.String(length=2048), nullable=True),
        sa.Column("source_notes_html_object_key", sa.String(length=2048), nullable=True),
        sa.Column("artifact_object_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_provider", sa.String(length=50), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("sandbox_provider", sa.String(length=50), nullable=True),
        sa.Column("sandbox_id", sa.String(length=255), nullable=True),
        sa.Column("agent_log_object_key", sa.String(length=2048), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_deck_runs_deck_id", "learning_deck_runs", ["deck_id"])
    op.create_index("ix_learning_deck_runs_user_id", "learning_deck_runs", ["user_id"])
    op.create_index("ix_learning_deck_runs_status", "learning_deck_runs", ["status"])
    op.create_index(
        "idx_learning_deck_runs_deck_created",
        "learning_deck_runs",
        ["deck_id", "created_at"],
    )
    op.create_index(
        "idx_learning_deck_runs_user_status",
        "learning_deck_runs",
        ["user_id", "status"],
    )
    op.create_index(
        "uq_learning_deck_runs_user_active",
        "learning_deck_runs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued', 'preparing', 'generating', 'validating', 'publishing')"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_learning_deck_runs_user_active", table_name="learning_deck_runs")
    op.drop_index("idx_learning_deck_runs_user_status", table_name="learning_deck_runs")
    op.drop_index("idx_learning_deck_runs_deck_created", table_name="learning_deck_runs")
    op.drop_index("ix_learning_deck_runs_status", table_name="learning_deck_runs")
    op.drop_index("ix_learning_deck_runs_user_id", table_name="learning_deck_runs")
    op.drop_index("ix_learning_deck_runs_deck_id", table_name="learning_deck_runs")
    op.drop_table("learning_deck_runs")

    op.drop_index("idx_learning_decks_user_updated", table_name="learning_decks")
    op.drop_index("uq_learning_decks_user_source_active", table_name="learning_decks")
    op.drop_index("ix_learning_decks_deleted_at", table_name="learning_decks")
    op.drop_index("ix_learning_decks_share_token_hash", table_name="learning_decks")
    op.drop_index("ix_learning_decks_share_enabled", table_name="learning_decks")
    op.drop_index("ix_learning_decks_latest_run_id", table_name="learning_decks")
    op.drop_index("ix_learning_decks_latest_successful_run_id", table_name="learning_decks")
    op.drop_index("ix_learning_decks_source_content_id", table_name="learning_decks")
    op.drop_index("ix_learning_decks_source_kind", table_name="learning_decks")
    op.drop_index("ix_learning_decks_user_id", table_name="learning_decks")
    op.drop_table("learning_decks")
