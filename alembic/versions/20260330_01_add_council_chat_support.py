"""Add council chat support."""


import sqlalchemy as sa

from alembic import op

revision: str = "20260330_01"
down_revision: str | None = "20260329_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("council_personas", sa.JSON(), nullable=True))

    op.add_column("chat_sessions", sa.Column("parent_session_id", sa.Integer(), nullable=True))
    op.add_column("chat_sessions", sa.Column("council_persona_id", sa.String(length=64), nullable=True))
    op.add_column(
        "chat_sessions", sa.Column("council_persona_name", sa.String(length=120), nullable=True)
    )
    op.add_column("chat_sessions", sa.Column("council_persona_prompt", sa.Text(), nullable=True))
    op.add_column(
        "chat_sessions",
        sa.Column("council_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("chat_sessions", sa.Column("active_child_session_id", sa.Integer(), nullable=True))
    op.add_column("chat_sessions", sa.Column("branch_start_message_id", sa.Integer(), nullable=True))
    op.add_column("chat_sessions", sa.Column("council_message_id", sa.Integer(), nullable=True))
    op.add_column(
        "chat_sessions",
        sa.Column("is_hidden_from_history", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_index(
        "ix_chat_sessions_parent_session_id", "chat_sessions", ["parent_session_id"], unique=False
    )
    op.create_index(
        "ix_chat_sessions_council_persona_id",
        "chat_sessions",
        ["council_persona_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_sessions_active_child_session_id",
        "chat_sessions",
        ["active_child_session_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_sessions_branch_start_message_id",
        "chat_sessions",
        ["branch_start_message_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_sessions_council_message_id",
        "chat_sessions",
        ["council_message_id"],
        unique=False,
    )
    op.create_index(
        "idx_chat_sessions_parent_hidden",
        "chat_sessions",
        ["parent_session_id", "is_hidden_from_history"],
        unique=False,
    )

    with op.batch_alter_table("chat_sessions") as batch_op:
        batch_op.alter_column("council_mode", server_default=None)
        batch_op.alter_column("is_hidden_from_history", server_default=None)


def downgrade() -> None:
    op.drop_index("idx_chat_sessions_parent_hidden", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_council_message_id", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_branch_start_message_id", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_active_child_session_id", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_council_persona_id", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_parent_session_id", table_name="chat_sessions")

    with op.batch_alter_table("chat_sessions") as batch_op:
        batch_op.drop_column("is_hidden_from_history")
        batch_op.drop_column("council_message_id")
        batch_op.drop_column("branch_start_message_id")
        batch_op.drop_column("active_child_session_id")
        batch_op.drop_column("council_mode")
        batch_op.drop_column("council_persona_prompt")
        batch_op.drop_column("council_persona_name")
        batch_op.drop_column("council_persona_id")
        batch_op.drop_column("parent_session_id")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("council_personas")
