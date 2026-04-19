"""migrate to user based tracking

Revision ID: f77c586ac45b
Revises: 31228d342949
Create Date: 2025-10-24 22:46:06.421302

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f77c586ac45b"
down_revision: str | None = "31228d342949"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Migrate from session_id to user_id."""
    # Delete all existing data (clean slate as per design decision)
    op.execute("DELETE FROM content_favorites")
    op.execute("DELETE FROM content_read_status")
    op.execute("DELETE FROM content_unlikes")

    # Drop and recreate content_favorites table
    op.drop_table("content_favorites")
    op.create_table(
        "content_favorites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column(
            "favorited_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "content_id", name="uq_content_favorites_user_content"),
    )
    op.create_index("ix_content_favorites_content_id", "content_favorites", ["content_id"])
    op.create_index("ix_content_favorites_user_id", "content_favorites", ["user_id"])

    # Drop and recreate content_read_status table
    op.drop_table("content_read_status")
    op.create_table(
        "content_read_status",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column(
            "read_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "content_id", name="uq_content_read_status_user_content"),
    )
    op.create_index("ix_content_read_status_content_id", "content_read_status", ["content_id"])
    op.create_index("ix_content_read_status_user_id", "content_read_status", ["user_id"])

    # Drop and recreate content_unlikes table
    op.drop_table("content_unlikes")
    op.create_table(
        "content_unlikes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column(
            "unliked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "content_id", name="uq_content_unlikes_user_content"),
    )
    op.create_index("ix_content_unlikes_content_id", "content_unlikes", ["content_id"])
    op.create_index("ix_content_unlikes_user_id", "content_unlikes", ["user_id"])


def downgrade() -> None:
    """Revert to session_id."""
    # Reverse the process
    op.execute("DELETE FROM content_favorites")
    op.execute("DELETE FROM content_read_status")
    op.execute("DELETE FROM content_unlikes")

    # Drop and recreate content_favorites with session_id
    op.drop_table("content_favorites")
    op.create_table(
        "content_favorites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(255), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column(
            "favorited_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "content_id", name="idx_content_favorites_session_content"
        ),
    )
    op.create_index("ix_content_favorites_content_id", "content_favorites", ["content_id"])
    op.create_index("ix_content_favorites_session_id", "content_favorites", ["session_id"])

    # Drop and recreate content_read_status with session_id
    op.drop_table("content_read_status")
    op.create_table(
        "content_read_status",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(255), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column(
            "read_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "content_id", name="idx_content_read_status_session_content"
        ),
    )
    op.create_index("ix_content_read_status_content_id", "content_read_status", ["content_id"])
    op.create_index("ix_content_read_status_session_id", "content_read_status", ["session_id"])

    # Drop and recreate content_unlikes with session_id
    op.drop_table("content_unlikes")
    op.create_table(
        "content_unlikes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(255), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column(
            "unliked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "content_id", name="idx_content_unlikes_session_content"),
    )
    op.create_index("ix_content_unlikes_content_id", "content_unlikes", ["content_id"])
    op.create_index("ix_content_unlikes_session_id", "content_unlikes", ["session_id"])
