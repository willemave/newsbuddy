"""add generic llm task ledger

Revision ID: 20260619_01
Revises: 20260609_01
Create Date: 2026-06-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260619_01"
down_revision: str | None = "20260609_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "llm_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_kind", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=64), nullable=False),
        sa.Column("workflow_key", sa.String(length=128), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("workflow_state", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("approval_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("allowed_actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tool_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("vm_namespace", sa.String(length=255), nullable=True),
        sa.Column("sandbox_provider", sa.String(length=50), nullable=True),
        sa.Column("sandbox_id", sa.String(length=255), nullable=True),
        sa.Column("workspace_path", sa.String(length=2048), nullable=True),
        sa.Column("shared_workspace_path", sa.String(length=2048), nullable=True),
        sa.Column("prompt_pack", sa.String(length=255), nullable=True),
        sa.Column("input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("artifact_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("usage_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status_history", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_provider", sa.String(length=50), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("agent_log_object_key", sa.String(length=2048), nullable=True),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_tasks_user_id", "llm_tasks", ["user_id"])
    op.create_index("ix_llm_tasks_task_kind", "llm_tasks", ["task_kind"])
    op.create_index("ix_llm_tasks_mode", "llm_tasks", ["mode"])
    op.create_index("ix_llm_tasks_workflow_key", "llm_tasks", ["workflow_key"])
    op.create_index("ix_llm_tasks_workflow_state", "llm_tasks", ["workflow_state"])
    op.create_index("ix_llm_tasks_status", "llm_tasks", ["status"])
    op.create_index("ix_llm_tasks_vm_namespace", "llm_tasks", ["vm_namespace"])
    op.create_index("ix_llm_tasks_sandbox_id", "llm_tasks", ["sandbox_id"])
    op.create_index(
        "idx_llm_tasks_user_status_created",
        "llm_tasks",
        ["user_id", "status", "created_at"],
    )
    op.create_index(
        "idx_llm_tasks_kind_mode_created",
        "llm_tasks",
        ["task_kind", "mode", "created_at"],
    )
    op.create_index(
        "idx_llm_tasks_workflow_state",
        "llm_tasks",
        ["workflow_key", "workflow_state"],
    )

    op.create_table(
        "llm_task_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("llm_task_id", sa.Integer(), nullable=False),
        sa.Column("action_name", sa.String(length=128), nullable=False),
        sa.Column("action_status", sa.String(length=32), nullable=False),
        sa.Column("approval_policy", sa.String(length=32), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("action_input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("action_result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=True),
        sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["llm_task_id"], ["llm_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_task_actions_llm_task_id", "llm_task_actions", ["llm_task_id"])
    op.create_index("ix_llm_task_actions_action_name", "llm_task_actions", ["action_name"])
    op.create_index(
        "ix_llm_task_actions_action_status",
        "llm_task_actions",
        ["action_status"],
    )
    op.create_index(
        "ix_llm_task_actions_idempotency_key",
        "llm_task_actions",
        ["idempotency_key"],
    )
    op.create_index(
        "ix_llm_task_actions_approved_by_user_id",
        "llm_task_actions",
        ["approved_by_user_id"],
    )
    op.create_index(
        "idx_llm_task_actions_task_status",
        "llm_task_actions",
        ["llm_task_id", "action_status", "created_at"],
    )
    op.create_index(
        "uq_llm_task_actions_idempotency",
        "llm_task_actions",
        ["llm_task_id", "action_name", "idempotency_key"],
        unique=True,
    )

    op.add_column("learning_deck_runs", sa.Column("llm_task_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_learning_deck_runs_llm_task_id",
        "learning_deck_runs",
        ["llm_task_id"],
    )
    op.create_foreign_key(
        "fk_learning_deck_runs_llm_task_id",
        "learning_deck_runs",
        "llm_tasks",
        ["llm_task_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "fk_learning_deck_runs_llm_task_id",
        "learning_deck_runs",
        type_="foreignkey",
    )
    op.drop_index("ix_learning_deck_runs_llm_task_id", table_name="learning_deck_runs")
    op.drop_column("learning_deck_runs", "llm_task_id")

    op.drop_index("uq_llm_task_actions_idempotency", table_name="llm_task_actions")
    op.drop_index("idx_llm_task_actions_task_status", table_name="llm_task_actions")
    op.drop_index("ix_llm_task_actions_approved_by_user_id", table_name="llm_task_actions")
    op.drop_index("ix_llm_task_actions_idempotency_key", table_name="llm_task_actions")
    op.drop_index("ix_llm_task_actions_action_status", table_name="llm_task_actions")
    op.drop_index("ix_llm_task_actions_action_name", table_name="llm_task_actions")
    op.drop_index("ix_llm_task_actions_llm_task_id", table_name="llm_task_actions")
    op.drop_table("llm_task_actions")

    op.drop_index("idx_llm_tasks_workflow_state", table_name="llm_tasks")
    op.drop_index("idx_llm_tasks_kind_mode_created", table_name="llm_tasks")
    op.drop_index("idx_llm_tasks_user_status_created", table_name="llm_tasks")
    op.drop_index("ix_llm_tasks_sandbox_id", table_name="llm_tasks")
    op.drop_index("ix_llm_tasks_vm_namespace", table_name="llm_tasks")
    op.drop_index("ix_llm_tasks_status", table_name="llm_tasks")
    op.drop_index("ix_llm_tasks_workflow_state", table_name="llm_tasks")
    op.drop_index("ix_llm_tasks_workflow_key", table_name="llm_tasks")
    op.drop_index("ix_llm_tasks_mode", table_name="llm_tasks")
    op.drop_index("ix_llm_tasks_task_kind", table_name="llm_tasks")
    op.drop_index("ix_llm_tasks_user_id", table_name="llm_tasks")
    op.drop_table("llm_tasks")
