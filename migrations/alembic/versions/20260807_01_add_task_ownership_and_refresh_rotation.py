"""Add task ownership/access grants and refresh-token rotation state.

Revision ID: 20260807_01
Revises: 20260730_02
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_01"
down_revision: str | None = "20260730_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create ownership fences, job-access grants, and replay protection."""
    op.add_column(
        "processing_tasks",
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_processing_tasks_owner_user_id_users",
        "processing_tasks",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_processing_tasks_owner_user_id",
        "processing_tasks",
        ["owner_user_id"],
    )

    op.create_table(
        "processing_task_user_access",
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["processing_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id", "user_id"),
    )
    op.create_index(
        "idx_processing_task_user_access_user_task",
        "processing_task_user_access",
        ["user_id", "task_id"],
    )

    op.create_table(
        "consumed_refresh_tokens",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index(
        "ix_consumed_refresh_tokens_user_id",
        "consumed_refresh_tokens",
        ["user_id"],
    )
    op.create_index(
        "idx_consumed_refresh_tokens_expiry",
        "consumed_refresh_tokens",
        ["expires_at"],
    )

    op.execute(
        sa.text(
            """
            WITH parsed_task_owners AS (
                SELECT id,
                       CASE
                           WHEN raw_user_id ~ '^[1-9][0-9]{0,9}$'
                               THEN CASE
                                   WHEN raw_user_id::bigint <= 2147483647
                                       THEN raw_user_id::integer
                               END
                       END AS user_id
                FROM (
                    SELECT id, payload ->> 'user_id' AS raw_user_id
                    FROM processing_tasks
                    WHERE task_type IN (
                        'backfill_feeds',
                        'discover_feeds',
                        'onboarding_discover',
                        'dig_deeper',
                        'sync_integration',
                        'generate_audio_episode',
                        'run_llm_task',
                        'briefing_refresh'
                    )
                ) AS task_payloads
            )
            UPDATE processing_tasks AS task
            SET owner_user_id = parsed.user_id
            FROM parsed_task_owners AS parsed
            JOIN users AS owner_user ON owner_user.id = parsed.user_id
            WHERE task.id = parsed.id
            """
        )
    )
    op.execute(
        """
        WITH parsed_audio_tasks AS (
            SELECT id,
                   CASE
                       WHEN raw_episode_id ~ '^[1-9][0-9]{0,9}$'
                           THEN CASE
                               WHEN raw_episode_id::bigint <= 2147483647
                                   THEN raw_episode_id::integer
                           END
                   END AS audio_episode_id
            FROM (
                SELECT id, payload ->> 'audio_episode_id' AS raw_episode_id
                FROM processing_tasks
                WHERE task_type = 'generate_audio_episode'
            ) AS task_payloads
        )
        UPDATE processing_tasks AS task
        SET owner_user_id = episode.user_id,
            payload = jsonb_set(
                COALESCE(task.payload, '{}'::json)::jsonb,
                '{user_id}',
                to_jsonb(episode.user_id),
                true
            )::json
        FROM parsed_audio_tasks AS parsed
        JOIN audio_episodes AS episode ON episode.id = parsed.audio_episode_id
        WHERE task.id = parsed.id
        """
    )
    op.execute(
        """
        INSERT INTO processing_task_user_access (task_id, user_id, created_at)
        SELECT id, owner_user_id, COALESCE(created_at, timezone('UTC', now()))
        FROM processing_tasks
        WHERE owner_user_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO processing_task_user_access (task_id, user_id, created_at)
        SELECT DISTINCT task.id, state.user_id, COALESCE(task.created_at, timezone('UTC', now()))
        FROM processing_tasks AS task
        JOIN (
            SELECT user_id, content_id FROM content_status
            UNION
            SELECT user_id, content_id FROM content_knowledge_saves
            UNION
            SELECT user_id, content_id FROM content_read_status
        ) AS state ON state.content_id = task.content_id
        JOIN users AS access_user ON access_user.id = state.user_id
        WHERE task.content_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )

    op.create_foreign_key(
        "fk_vendor_usage_records_user_id_users",
        "vendor_usage_records",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )
    # NOT VALID permits existing orphan rows but immediately fences concurrent
    # inserts, so the cleanup cannot race a new invalid user reference.
    op.execute(
        """
        UPDATE vendor_usage_records AS usage
        SET user_id = NULL
        WHERE user_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = usage.user_id)
        """
    )
    # Entering an autocommit block commits the preceding ADD ... NOT VALID so
    # its SHARE ROW EXCLUSIVE lock is released before the table scan begins.
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                "ALTER TABLE vendor_usage_records "
                "VALIDATE CONSTRAINT fk_vendor_usage_records_user_id_users"
            )
        )


def downgrade() -> None:
    """Remove task ownership and refresh-token rotation state."""
    op.drop_constraint(
        "fk_vendor_usage_records_user_id_users",
        "vendor_usage_records",
        type_="foreignkey",
    )
    op.drop_index("idx_consumed_refresh_tokens_expiry", table_name="consumed_refresh_tokens")
    op.drop_index("ix_consumed_refresh_tokens_user_id", table_name="consumed_refresh_tokens")
    op.drop_table("consumed_refresh_tokens")
    op.drop_index(
        "idx_processing_task_user_access_user_task",
        table_name="processing_task_user_access",
    )
    op.drop_table("processing_task_user_access")
    op.drop_index("ix_processing_tasks_owner_user_id", table_name="processing_tasks")
    op.drop_constraint(
        "fk_processing_tasks_owner_user_id_users",
        "processing_tasks",
        type_="foreignkey",
    )
    op.drop_column("processing_tasks", "owner_user_id")
