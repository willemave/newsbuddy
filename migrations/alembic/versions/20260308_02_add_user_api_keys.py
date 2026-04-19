"""Add user API keys table.

Revision ID: 20260308_02
Revises: 20260308_01
Create Date: 2026-03-08
"""

import sqlalchemy as sa
from alembic import op

revision = "20260308_02"
down_revision = "20260308_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key_prefix", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=128), nullable=False),
        sa.Column("created_by_admin_user_id", sa.Integer(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_api_keys_created_by_admin_user_id"),
        "user_api_keys",
        ["created_by_admin_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_api_keys_key_prefix"), "user_api_keys", ["key_prefix"], unique=False
    )
    op.create_index(op.f("ix_user_api_keys_user_id"), "user_api_keys", ["user_id"], unique=False)
    op.create_index(
        "idx_user_api_keys_prefix_revoked",
        "user_api_keys",
        ["key_prefix", "revoked_at"],
        unique=False,
    )
    op.create_index(
        "idx_user_api_keys_user_revoked",
        "user_api_keys",
        ["user_id", "revoked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_user_api_keys_user_revoked", table_name="user_api_keys")
    op.drop_index("idx_user_api_keys_prefix_revoked", table_name="user_api_keys")
    op.drop_index(op.f("ix_user_api_keys_user_id"), table_name="user_api_keys")
    op.drop_index(op.f("ix_user_api_keys_key_prefix"), table_name="user_api_keys")
    op.drop_index(
        op.f("ix_user_api_keys_created_by_admin_user_id"),
        table_name="user_api_keys",
    )
    op.drop_table("user_api_keys")
