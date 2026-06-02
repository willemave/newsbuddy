"""add audio episode share fields

Revision ID: 20260602_01
Revises: 20260527_01
Create Date: 2026-06-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260602_01"
down_revision: str | None = "20260527_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "audio_episodes",
        sa.Column(
            "share_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "audio_episodes",
        sa.Column("share_token_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "audio_episodes",
        sa.Column("share_token_nonce", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_audio_episodes_share_enabled", "audio_episodes", ["share_enabled"])
    op.create_index("ix_audio_episodes_share_token_hash", "audio_episodes", ["share_token_hash"])
    op.alter_column("audio_episodes", "share_enabled", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_audio_episodes_share_token_hash", table_name="audio_episodes")
    op.drop_index("ix_audio_episodes_share_enabled", table_name="audio_episodes")
    op.drop_column("audio_episodes", "share_token_nonce")
    op.drop_column("audio_episodes", "share_token_hash")
    op.drop_column("audio_episodes", "share_enabled")
