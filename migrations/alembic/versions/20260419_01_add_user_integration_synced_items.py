"""add user integration synced items ledger

Revision ID: 20260419_01
Revises: 20260414_01
Create Date: 2026-04-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260419_01"
down_revision: str | None = "20260414_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_integration_synced_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("external_item_id", sa.String(length=255), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=True),
        sa.Column("item_url", sa.String(length=2048), nullable=True),
        sa.Column("first_synced_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id",
            "channel",
            "external_item_id",
            name="uq_user_integration_synced_item",
        ),
    )
    op.create_index(
        "idx_user_integration_synced_item_lookup",
        "user_integration_synced_items",
        ["connection_id", "channel", "last_seen_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_integration_synced_items_channel"),
        "user_integration_synced_items",
        ["channel"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_integration_synced_items_connection_id"),
        "user_integration_synced_items",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_integration_synced_items_content_id"),
        "user_integration_synced_items",
        ["content_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_integration_synced_items_external_item_id"),
        "user_integration_synced_items",
        ["external_item_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_user_integration_synced_items_external_item_id"),
        table_name="user_integration_synced_items",
    )
    op.drop_index(
        op.f("ix_user_integration_synced_items_content_id"),
        table_name="user_integration_synced_items",
    )
    op.drop_index(
        op.f("ix_user_integration_synced_items_connection_id"),
        table_name="user_integration_synced_items",
    )
    op.drop_index(
        op.f("ix_user_integration_synced_items_channel"),
        table_name="user_integration_synced_items",
    )
    op.drop_index(
        "idx_user_integration_synced_item_lookup",
        table_name="user_integration_synced_items",
    )
    op.drop_table("user_integration_synced_items")
