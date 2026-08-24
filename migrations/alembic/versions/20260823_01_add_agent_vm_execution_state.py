"""Add persistent agent VM and user-data synchronization state.

Revision ID: 20260823_01
Revises: 20260822_01
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260823_01"
down_revision: str | None = "20260822_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Persist VM identity, corpus revisions, and separate tool progress."""
    op.add_column("users", sa.Column("agent_vm_sandbox_id", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("agent_vm_template_revision", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("agent_vm_snapshot_id", sa.String(255), nullable=True))
    op.add_column(
        "users",
        sa.Column("agent_vm_snapshot_template_revision", sa.String(255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("agent_data_revision", sa.BigInteger(), server_default="0", nullable=False),
    )

    op.add_column("chat_messages", sa.Column("tool_progress", postgresql.JSONB()))
    op.add_column("chat_messages", sa.Column("tool_progress_revision", sa.Integer()))
    op.add_column(
        "chat_messages", sa.Column("tool_progress_updated_at", sa.DateTime(timezone=True))
    )

    op.create_table(
        "agent_vm_system_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sandbox_id", sa.String(255)),
        sa.Column("template_revision", sa.String(255)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "agent_data_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("document_kind", sa.String(32), nullable=False),
        sa.Column("document_key", sa.String(255), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column(
            "stale_paths",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("index_record", postgresql.JSONB(), nullable=False),
        sa.Column("byte_size", sa.Integer(), server_default="0", nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "path", name="uq_agent_data_files_user_path"),
        sa.UniqueConstraint(
            "user_id",
            "document_kind",
            "document_key",
            name="uq_agent_data_files_user_document",
        ),
    )
    op.create_index(
        "idx_agent_data_files_user_revision", "agent_data_files", ["user_id", "revision"]
    )


def downgrade() -> None:
    """Remove persistent agent VM state."""
    op.drop_index("idx_agent_data_files_user_revision", table_name="agent_data_files")
    op.drop_table("agent_data_files")
    op.drop_table("agent_vm_system_state")
    op.drop_column("chat_messages", "tool_progress_updated_at")
    op.drop_column("chat_messages", "tool_progress_revision")
    op.drop_column("chat_messages", "tool_progress")
    op.drop_column("users", "agent_data_revision")
    op.drop_column("users", "agent_vm_snapshot_template_revision")
    op.drop_column("users", "agent_vm_snapshot_id")
    op.drop_column("users", "agent_vm_template_revision")
    op.drop_column("users", "agent_vm_sandbox_id")
