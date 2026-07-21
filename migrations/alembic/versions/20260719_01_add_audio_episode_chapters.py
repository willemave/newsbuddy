"""Add chapter grouping to audio episodes.

Revision ID: 20260719_01
Revises: 20260717_01
Create Date: 2026-07-19 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_01"
down_revision: str | None = "20260717_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audio_episodes",
        sa.Column("episode_group_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "audio_episodes",
        sa.Column("chapter_index", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_audio_episodes_chapter_metadata_pair",
        "audio_episodes",
        "(episode_group_id IS NULL) = (chapter_index IS NULL)",
    )
    op.create_check_constraint(
        "ck_audio_episodes_chapter_index_nonnegative",
        "audio_episodes",
        "chapter_index IS NULL OR chapter_index >= 0",
    )
    op.create_unique_constraint(
        "uq_audio_episodes_user_kind_group_chapter",
        "audio_episodes",
        ["user_id", "kind", "episode_group_id", "chapter_index"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_audio_episodes_user_kind_group_chapter",
        "audio_episodes",
        type_="unique",
    )
    op.drop_constraint(
        "ck_audio_episodes_chapter_index_nonnegative",
        "audio_episodes",
        type_="check",
    )
    op.drop_constraint(
        "ck_audio_episodes_chapter_metadata_pair",
        "audio_episodes",
        type_="check",
    )
    op.drop_column("audio_episodes", "chapter_index")
    op.drop_column("audio_episodes", "episode_group_id")
