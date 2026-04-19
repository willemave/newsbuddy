"""Initial schema setup

Revision ID: 001_initial_schema
Revises:
Create Date: 2025-01-04 03:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: str | None = "824291a177f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Check if we're upgrading from old migrations
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_tables = inspector.get_table_names()

    # If tables already exist (from old migrations), this is a no-op
    if "contents" in existing_tables:
        # Tables already exist from previous migrations
        return

    # Create contents table
    op.create_table(
        "contents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=20), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="new"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("classification", sa.String(length=20), nullable=True),
        sa.Column("checked_out_by", sa.String(length=100), nullable=True),
        sa.Column("checked_out_at", sa.DateTime(), nullable=True),
        sa.Column("content_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )

    # Create indexes for contents table
    op.create_index("idx_checkout", "contents", ["checked_out_by", "checked_out_at"], unique=False)
    op.create_index("idx_content_type_status", "contents", ["content_type", "status"], unique=False)
    op.create_index("idx_created_at", "contents", ["created_at"], unique=False)
    op.create_index(
        op.f("ix_contents_checked_out_by"), "contents", ["checked_out_by"], unique=False
    )
    op.create_index(
        op.f("ix_contents_classification"), "contents", ["classification"], unique=False
    )
    op.create_index(op.f("ix_contents_content_type"), "contents", ["content_type"], unique=False)
    op.create_index(op.f("ix_contents_source"), "contents", ["source"], unique=False)
    op.create_index(op.f("ix_contents_status"), "contents", ["status"], unique=False)

    # Create event_logs table
    op.create_table(
        "event_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("event_name", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for event_logs table
    op.create_index(
        "idx_event_name_created", "event_logs", ["event_name", "created_at"], unique=False
    )
    op.create_index(
        "idx_event_status_created",
        "event_logs",
        ["event_type", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_event_type_created", "event_logs", ["event_type", "created_at"], unique=False
    )
    op.create_index(op.f("ix_event_logs_created_at"), "event_logs", ["created_at"], unique=False)
    op.create_index(op.f("ix_event_logs_event_name"), "event_logs", ["event_name"], unique=False)
    op.create_index(op.f("ix_event_logs_event_type"), "event_logs", ["event_type"], unique=False)
    op.create_index(op.f("ix_event_logs_status"), "event_logs", ["status"], unique=False)

    # Create processing_tasks table
    op.create_table(
        "processing_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(length=50), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column("status", sa.String(length=20), nullable=True, server_default="pending"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=True, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for processing_tasks table
    op.create_index(
        "idx_task_status_created", "processing_tasks", ["status", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_processing_tasks_content_id"), "processing_tasks", ["content_id"], unique=False
    )
    op.create_index(
        op.f("ix_processing_tasks_status"), "processing_tasks", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_processing_tasks_task_type"), "processing_tasks", ["task_type"], unique=False
    )


def downgrade() -> None:
    # Drop processing_tasks table and indexes
    op.drop_index(op.f("ix_processing_tasks_task_type"), table_name="processing_tasks")
    op.drop_index(op.f("ix_processing_tasks_status"), table_name="processing_tasks")
    op.drop_index(op.f("ix_processing_tasks_content_id"), table_name="processing_tasks")
    op.drop_index("idx_task_status_created", table_name="processing_tasks")
    op.drop_table("processing_tasks")

    # Drop event_logs table and indexes
    op.drop_index(op.f("ix_event_logs_status"), table_name="event_logs")
    op.drop_index(op.f("ix_event_logs_event_type"), table_name="event_logs")
    op.drop_index(op.f("ix_event_logs_event_name"), table_name="event_logs")
    op.drop_index(op.f("ix_event_logs_created_at"), table_name="event_logs")
    op.drop_index("idx_event_type_created", table_name="event_logs")
    op.drop_index("idx_event_status_created", table_name="event_logs")
    op.drop_index("idx_event_name_created", table_name="event_logs")
    op.drop_table("event_logs")

    # Drop contents table and indexes
    op.drop_index(op.f("ix_contents_status"), table_name="contents")
    op.drop_index(op.f("ix_contents_source"), table_name="contents")
    op.drop_index(op.f("ix_contents_content_type"), table_name="contents")
    op.drop_index(op.f("ix_contents_classification"), table_name="contents")
    op.drop_index(op.f("ix_contents_checked_out_by"), table_name="contents")
    op.drop_index("idx_created_at", table_name="contents")
    op.drop_index("idx_content_type_status", table_name="contents")
    op.drop_index("idx_checkout", table_name="contents")
    op.drop_table("contents")
