"""add audio episodes

Revision ID: 20260513_01
Revises: 20260509_02
Create Date: 2026-05-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260513_01"
down_revision: str | None = "20260509_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "audio_episodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_content_id", sa.Integer(), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("source_item_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("script", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("script_text", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("audio_storage_path", sa.String(length=2048), nullable=True),
        sa.Column("audio_content_type", sa.String(length=100), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
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
        sa.UniqueConstraint(
            "user_id", "kind", "input_hash", name="uq_audio_episodes_user_kind_hash"
        ),
    )
    op.create_index(
        "idx_audio_episodes_user_kind_status",
        "audio_episodes",
        ["user_id", "kind", "status"],
        unique=False,
    )
    op.create_index("ix_audio_episodes_kind", "audio_episodes", ["kind"], unique=False)
    op.create_index(
        "ix_audio_episodes_source_content_id", "audio_episodes", ["source_content_id"], unique=False
    )
    op.create_index("ix_audio_episodes_status", "audio_episodes", ["status"], unique=False)
    op.create_index("ix_audio_episodes_user_id", "audio_episodes", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_audio_episodes_user_id", table_name="audio_episodes")
    op.drop_index("ix_audio_episodes_status", table_name="audio_episodes")
    op.drop_index("ix_audio_episodes_source_content_id", table_name="audio_episodes")
    op.drop_index("ix_audio_episodes_kind", table_name="audio_episodes")
    op.drop_index("idx_audio_episodes_user_kind_status", table_name="audio_episodes")
    op.drop_table("audio_episodes")
