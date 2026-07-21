"""Reconcile Learning Deck attempts left active by failed queue deliveries.

Revision ID: 20260719_02
Revises: 20260719_01
Create Date: 2026-07-19 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_02"
down_revision: str | None = "20260719_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Terminalize historical rows once; handlers own this transition afterward."""
    op.execute(
        sa.text(
            """
            WITH failed_delivery AS (
                SELECT DISTINCT ON ((payload ->> 'llm_task_id')::integer)
                    (payload ->> 'llm_task_id')::integer AS llm_task_id,
                    error_message
                FROM processing_tasks
                WHERE task_type = 'run_llm_task'
                  AND status = 'failed'
                  AND payload ->> 'llm_task_id' ~ '^[0-9]+$'
                ORDER BY (payload ->> 'llm_task_id')::integer, id DESC
            )
            UPDATE llm_tasks AS task
            SET status = 'failed',
                workflow_state = 'failed',
                error_type = 'queue_task_failed',
                error_message = LEFT(
                    COALESCE(failed_delivery.error_message, 'Learning Deck queue task failed'),
                    4000
                ),
                completed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP,
                status_history = COALESCE(task.status_history, '[]'::jsonb) ||
                    jsonb_build_array(
                        jsonb_build_object(
                            'status', 'failed',
                            'workflow_state', 'failed',
                            'note', 'Learning Deck generation failed',
                            'created_at', CURRENT_TIMESTAMP
                        )
                    )
            FROM failed_delivery
            WHERE task.id = failed_delivery.llm_task_id
              AND task.task_kind = 'learning_deck'
              AND task.status IN (
                  'queued', 'preparing', 'running', 'awaiting_approval', 'applying'
              )
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH failed_delivery AS (
                SELECT DISTINCT ON ((payload ->> 'learning_deck_run_id')::integer)
                    (payload ->> 'learning_deck_run_id')::integer AS run_id,
                    error_message
                FROM processing_tasks
                WHERE task_type = 'generate_learning_deck'
                  AND status = 'failed'
                  AND payload ->> 'learning_deck_run_id' ~ '^[0-9]+$'
                ORDER BY (payload ->> 'learning_deck_run_id')::integer, id DESC
            )
            UPDATE learning_deck_runs AS run
            SET status = 'failed',
                error_message = LEFT(
                    COALESCE(failed_delivery.error_message, 'Learning Deck queue task failed'),
                    4000
                ),
                completed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP,
                timeline = COALESCE(run.timeline, '[]'::jsonb) ||
                    jsonb_build_array(
                        jsonb_build_object(
                            'status', 'failed',
                            'note', 'Learning Deck generation failed',
                            'created_at', CURRENT_TIMESTAMP
                        )
                    )
            FROM failed_delivery
            WHERE run.id = failed_delivery.run_id
              AND run.status IN (
                  'queued', 'preparing', 'generating', 'validating', 'publishing'
              )
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH failed_delivery AS (
                SELECT DISTINCT ON ((payload ->> 'learning_deck_run_id')::integer)
                    (payload ->> 'learning_deck_run_id')::integer AS run_id,
                    error_message
                FROM processing_tasks
                WHERE task_type = 'generate_learning_deck'
                  AND status = 'failed'
                  AND payload ->> 'learning_deck_run_id' ~ '^[0-9]+$'
                ORDER BY (payload ->> 'learning_deck_run_id')::integer, id DESC
            )
            UPDATE llm_tasks AS task
            SET status = 'failed',
                workflow_state = 'failed',
                error_type = 'learning_deck_generation_failed',
                error_message = LEFT(
                    COALESCE(failed_delivery.error_message, 'Learning Deck queue task failed'),
                    4000
                ),
                completed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP,
                status_history = COALESCE(task.status_history, '[]'::jsonb) ||
                    jsonb_build_array(
                        jsonb_build_object(
                            'status', 'failed',
                            'workflow_state', 'failed',
                            'note', 'Learning Deck generation failed',
                            'created_at', CURRENT_TIMESTAMP
                        )
                    )
            FROM learning_deck_runs AS run, failed_delivery
            WHERE run.id = failed_delivery.run_id
              AND task.id = run.llm_task_id
              AND task.status IN (
                  'queued', 'preparing', 'running', 'awaiting_approval', 'applying'
              )
            """
        )
    )


def downgrade() -> None:
    """Historical terminal transitions are intentionally irreversible."""
