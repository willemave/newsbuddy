"""Add llm_usage_records table."""

import sqlalchemy as sa
from alembic import op

revision: str = "20260328_01"
down_revision: str | None = "20260327_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_usage_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("feature", sa.String(length=100), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("content_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
        sa.Column("pricing_version", sa.String(length=50), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_usage_records_created_at", "llm_usage_records", ["created_at"])
    op.create_index("ix_llm_usage_records_provider", "llm_usage_records", ["provider"])
    op.create_index("ix_llm_usage_records_model", "llm_usage_records", ["model"])
    op.create_index("ix_llm_usage_records_feature", "llm_usage_records", ["feature"])
    op.create_index("ix_llm_usage_records_operation", "llm_usage_records", ["operation"])
    op.create_index("ix_llm_usage_records_source", "llm_usage_records", ["source"])
    op.create_index("ix_llm_usage_records_request_id", "llm_usage_records", ["request_id"])
    op.create_index("ix_llm_usage_records_task_id", "llm_usage_records", ["task_id"])
    op.create_index("ix_llm_usage_records_content_id", "llm_usage_records", ["content_id"])
    op.create_index("ix_llm_usage_records_session_id", "llm_usage_records", ["session_id"])
    op.create_index("ix_llm_usage_records_message_id", "llm_usage_records", ["message_id"])
    op.create_index("ix_llm_usage_records_user_id", "llm_usage_records", ["user_id"])
    op.create_index(
        "idx_llm_usage_provider_model_created",
        "llm_usage_records",
        ["provider", "model", "created_at"],
    )
    op.create_index(
        "idx_llm_usage_content_created",
        "llm_usage_records",
        ["content_id", "created_at"],
    )
    op.create_index(
        "idx_llm_usage_session_created",
        "llm_usage_records",
        ["session_id", "created_at"],
    )
    op.create_index(
        "idx_llm_usage_task_created",
        "llm_usage_records",
        ["task_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_llm_usage_task_created", table_name="llm_usage_records")
    op.drop_index("idx_llm_usage_session_created", table_name="llm_usage_records")
    op.drop_index("idx_llm_usage_content_created", table_name="llm_usage_records")
    op.drop_index("idx_llm_usage_provider_model_created", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_user_id", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_message_id", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_session_id", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_content_id", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_task_id", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_request_id", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_source", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_operation", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_feature", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_model", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_provider", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_created_at", table_name="llm_usage_records")
    op.drop_table("llm_usage_records")
