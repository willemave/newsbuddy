"""Add CLI QR link sessions table.

Revision ID: 20260404_01
Revises: 20260401_01
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

revision = "20260404_01"
down_revision = "20260401_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cli_link_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("approve_token_hash", sa.String(length=128), nullable=False),
        sa.Column("poll_token_hash", sa.String(length=128), nullable=False),
        sa.Column("requested_device_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("user_api_key_id", sa.Integer(), nullable=True),
        sa.Column("issued_api_key_plaintext", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_cli_link_sessions_approved_by_user_id"),
        "cli_link_sessions",
        ["approved_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cli_link_sessions_expires_at"), "cli_link_sessions", ["expires_at"], unique=False
    )
    op.create_index(
        op.f("ix_cli_link_sessions_session_id"), "cli_link_sessions", ["session_id"], unique=True
    )
    op.create_index(
        op.f("ix_cli_link_sessions_user_api_key_id"),
        "cli_link_sessions",
        ["user_api_key_id"],
        unique=False,
    )
    op.create_index(
        "idx_cli_link_sessions_status_expires",
        "cli_link_sessions",
        ["status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_cli_link_sessions_status_expires", table_name="cli_link_sessions")
    op.drop_index(op.f("ix_cli_link_sessions_user_api_key_id"), table_name="cli_link_sessions")
    op.drop_index(op.f("ix_cli_link_sessions_session_id"), table_name="cli_link_sessions")
    op.drop_index(op.f("ix_cli_link_sessions_expires_at"), table_name="cli_link_sessions")
    op.drop_index(op.f("ix_cli_link_sessions_approved_by_user_id"), table_name="cli_link_sessions")
    op.drop_table("cli_link_sessions")
