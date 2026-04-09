"""add_chat_tables

Revision ID: 1620a8545d8c
Revises: 20250630_02
Create Date: 2025-11-28 12:55:58.174961

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1620a8545d8c"
down_revision: str | None = "20250630_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Create chat_sessions table
    if not _table_exists(inspector, "chat_sessions"):
        op.create_table(
            "chat_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False, index=True),
            sa.Column("content_id", sa.Integer(), nullable=True, index=True),
            sa.Column("title", sa.String(500), nullable=True),
            sa.Column("session_type", sa.String(50), nullable=True),
            sa.Column("topic", sa.String(500), nullable=True),
            sa.Column("llm_model", sa.String(100), nullable=False, server_default="openai:gpt-5.4"),
            sa.Column("llm_provider", sa.String(50), nullable=False, server_default="openai"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=True, onupdate=sa.func.now()),
            sa.Column("last_message_at", sa.DateTime(), nullable=True, index=True),
            sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    if not _index_exists(inspector, "chat_sessions", "idx_chat_sessions_user_time"):
        op.create_index(
            "idx_chat_sessions_user_time",
            "chat_sessions",
            ["user_id", "last_message_at"],
        )
    if not _index_exists(inspector, "chat_sessions", "idx_chat_sessions_content"):
        op.create_index(
            "idx_chat_sessions_content",
            "chat_sessions",
            ["user_id", "content_id"],
        )

    # Create chat_messages table
    if not _table_exists(inspector, "chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("session_id", sa.Integer(), nullable=False, index=True),
            sa.Column("message_list", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    if not _index_exists(inspector, "chat_messages", "idx_chat_messages_session_created"):
        op.create_index(
            "idx_chat_messages_session_created",
            "chat_messages",
            ["session_id", "created_at"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _index_exists(inspector, "chat_messages", "idx_chat_messages_session_created"):
        op.drop_index("idx_chat_messages_session_created", table_name="chat_messages")
    if _table_exists(inspector, "chat_messages"):
        op.drop_table("chat_messages")
    if _index_exists(inspector, "chat_sessions", "idx_chat_sessions_content"):
        op.drop_index("idx_chat_sessions_content", table_name="chat_sessions")
    if _index_exists(inspector, "chat_sessions", "idx_chat_sessions_user_time"):
        op.drop_index("idx_chat_sessions_user_time", table_name="chat_sessions")
    if _table_exists(inspector, "chat_sessions"):
        op.drop_table("chat_sessions")


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return inspector.has_table(table_name)


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    existing = inspector.get_indexes(table_name)
    return any(idx.get("name") == index_name for idx in existing)
