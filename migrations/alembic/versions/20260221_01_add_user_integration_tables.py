"""add user integration tables and twitter username

Revision ID: 20260221_01
Revises: 20260218_01
Create Date: 2026-02-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260221_01"
down_revision: str | None = "20260218_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("twitter_username", sa.String(length=50), nullable=True))
    op.create_index(op.f("ix_users_twitter_username"), "users", ["twitter_username"], unique=False)

    op.create_table(
        "user_integration_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_user_id", sa.String(length=255), nullable=True),
        sa.Column("provider_username", sa.String(length=255), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=True),
        sa.Column("connection_metadata", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_provider_provider_user"),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_provider_connection"),
    )
    op.create_index(
        "idx_user_integration_provider_active",
        "user_integration_connections",
        ["provider", "is_active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_integration_connections_is_active"),
        "user_integration_connections",
        ["is_active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_integration_connections_provider"),
        "user_integration_connections",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_integration_connections_user_id"),
        "user_integration_connections",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "user_integration_sync_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("cursor", sa.String(length=1024), nullable=True),
        sa.Column("last_synced_item_id", sa.String(length=255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(length=50), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sync_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", name="uq_user_integration_sync_connection"),
    )
    op.create_index(
        "idx_user_integration_sync_last_synced",
        "user_integration_sync_state",
        ["last_synced_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_integration_sync_state_connection_id"),
        "user_integration_sync_state",
        ["connection_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_user_integration_sync_state_connection_id"),
        table_name="user_integration_sync_state",
    )
    op.drop_index("idx_user_integration_sync_last_synced", table_name="user_integration_sync_state")
    op.drop_table("user_integration_sync_state")

    op.drop_index(
        op.f("ix_user_integration_connections_user_id"), table_name="user_integration_connections"
    )
    op.drop_index(
        op.f("ix_user_integration_connections_provider"), table_name="user_integration_connections"
    )
    op.drop_index(
        op.f("ix_user_integration_connections_is_active"), table_name="user_integration_connections"
    )
    op.drop_index("idx_user_integration_provider_active", table_name="user_integration_connections")
    op.drop_table("user_integration_connections")

    op.drop_index(op.f("ix_users_twitter_username"), table_name="users")
    op.drop_column("users", "twitter_username")
