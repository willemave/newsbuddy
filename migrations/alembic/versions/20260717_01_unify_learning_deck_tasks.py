"""Make llm_tasks the owner of Learning Deck generation attempts.

Revision ID: 20260717_01
Revises: 20260712_01
Create Date: 2026-07-17 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_01"
down_revision: str | None = "20260712_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("llm_tasks", sa.Column("subject_id", sa.Integer(), nullable=True))
    op.add_column("llm_tasks", sa.Column("parent_task_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_llm_tasks_parent_task_id",
        "llm_tasks",
        "llm_tasks",
        ["parent_task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_llm_tasks_subject_id", "llm_tasks", ["subject_id"])
    op.create_index("ix_llm_tasks_parent_task_id", "llm_tasks", ["parent_task_id"])
    op.create_index(
        "idx_llm_tasks_kind_subject_created",
        "llm_tasks",
        ["task_kind", "subject_id", "created_at"],
    )
    op.create_index(
        "uq_llm_tasks_learning_deck_user_active",
        "llm_tasks",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "task_kind = 'learning_deck' AND "
            "status IN ('queued', 'preparing', 'running', 'awaiting_approval', 'applying')"
        ),
    )

    op.add_column(
        "learning_decks",
        sa.Column("latest_task_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "learning_decks",
        sa.Column("latest_successful_task_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_learning_decks_latest_task_id",
        "learning_decks",
        "llm_tasks",
        ["latest_task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_learning_decks_latest_successful_task_id",
        "learning_decks",
        "llm_tasks",
        ["latest_successful_task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_learning_decks_latest_task_id", "learning_decks", ["latest_task_id"])
    op.create_index(
        "ix_learning_decks_latest_successful_task_id",
        "learning_decks",
        ["latest_successful_task_id"],
    )

    op.execute(
        """
        UPDATE llm_tasks AS task
        SET subject_id = run.deck_id
        FROM learning_deck_runs AS run
        WHERE run.llm_task_id = task.id
          AND task.task_kind = 'learning_deck'
          AND task.subject_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE learning_decks AS deck
        SET latest_task_id = run.llm_task_id
        FROM learning_deck_runs AS run
        WHERE run.id = deck.latest_run_id
        """
    )
    op.execute(
        """
        UPDATE learning_decks AS deck
        SET latest_successful_task_id = run.llm_task_id
        FROM learning_deck_runs AS run
        WHERE run.id = deck.latest_successful_run_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_learning_decks_latest_successful_task_id", table_name="learning_decks")
    op.drop_index("ix_learning_decks_latest_task_id", table_name="learning_decks")
    op.drop_constraint(
        "fk_learning_decks_latest_successful_task_id",
        "learning_decks",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_learning_decks_latest_task_id",
        "learning_decks",
        type_="foreignkey",
    )
    op.drop_column("learning_decks", "latest_successful_task_id")
    op.drop_column("learning_decks", "latest_task_id")
    op.drop_index("idx_llm_tasks_kind_subject_created", table_name="llm_tasks")
    op.drop_index("uq_llm_tasks_learning_deck_user_active", table_name="llm_tasks")
    op.drop_index("ix_llm_tasks_parent_task_id", table_name="llm_tasks")
    op.drop_index("ix_llm_tasks_subject_id", table_name="llm_tasks")
    op.drop_constraint("fk_llm_tasks_parent_task_id", "llm_tasks", type_="foreignkey")
    op.drop_column("llm_tasks", "parent_task_id")
    op.drop_column("llm_tasks", "subject_id")
