"""Rename llm usage records to vendor usage records."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260414_01"
down_revision: str | None = "20260410_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("llm_usage_records", "vendor_usage_records")

    op.execute(
        "ALTER INDEX IF EXISTS ix_llm_usage_records_created_at "
        "RENAME TO ix_vendor_usage_records_created_at"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_llm_usage_records_provider "
        "RENAME TO ix_vendor_usage_records_provider"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_llm_usage_records_model RENAME TO ix_vendor_usage_records_model"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_llm_usage_records_feature "
        "RENAME TO ix_vendor_usage_records_feature"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_llm_usage_records_operation "
        "RENAME TO ix_vendor_usage_records_operation"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_llm_usage_records_source RENAME TO ix_vendor_usage_records_source"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_llm_usage_records_request_id "
        "RENAME TO ix_vendor_usage_records_request_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_llm_usage_records_task_id "
        "RENAME TO ix_vendor_usage_records_task_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_llm_usage_records_content_id "
        "RENAME TO ix_vendor_usage_records_content_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_llm_usage_records_session_id "
        "RENAME TO ix_vendor_usage_records_session_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_llm_usage_records_message_id "
        "RENAME TO ix_vendor_usage_records_message_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_llm_usage_records_user_id "
        "RENAME TO ix_vendor_usage_records_user_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS idx_llm_usage_provider_model_created "
        "RENAME TO idx_vendor_usage_provider_model_created"
    )
    op.execute(
        "ALTER INDEX IF EXISTS idx_llm_usage_content_created "
        "RENAME TO idx_vendor_usage_content_created"
    )
    op.execute(
        "ALTER INDEX IF EXISTS idx_llm_usage_session_created "
        "RENAME TO idx_vendor_usage_session_created"
    )
    op.execute(
        "ALTER INDEX IF EXISTS idx_llm_usage_task_created RENAME TO idx_vendor_usage_task_created"
    )

    op.add_column("vendor_usage_records", sa.Column("request_count", sa.Integer(), nullable=True))
    op.add_column("vendor_usage_records", sa.Column("resource_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("vendor_usage_records", "resource_count")
    op.drop_column("vendor_usage_records", "request_count")

    op.execute(
        "ALTER INDEX IF EXISTS idx_vendor_usage_task_created RENAME TO idx_llm_usage_task_created"
    )
    op.execute(
        "ALTER INDEX IF EXISTS idx_vendor_usage_session_created "
        "RENAME TO idx_llm_usage_session_created"
    )
    op.execute(
        "ALTER INDEX IF EXISTS idx_vendor_usage_content_created "
        "RENAME TO idx_llm_usage_content_created"
    )
    op.execute(
        "ALTER INDEX IF EXISTS idx_vendor_usage_provider_model_created "
        "RENAME TO idx_llm_usage_provider_model_created"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_vendor_usage_records_user_id "
        "RENAME TO ix_llm_usage_records_user_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_vendor_usage_records_message_id "
        "RENAME TO ix_llm_usage_records_message_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_vendor_usage_records_session_id "
        "RENAME TO ix_llm_usage_records_session_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_vendor_usage_records_content_id "
        "RENAME TO ix_llm_usage_records_content_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_vendor_usage_records_task_id "
        "RENAME TO ix_llm_usage_records_task_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_vendor_usage_records_request_id "
        "RENAME TO ix_llm_usage_records_request_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_vendor_usage_records_source RENAME TO ix_llm_usage_records_source"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_vendor_usage_records_operation "
        "RENAME TO ix_llm_usage_records_operation"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_vendor_usage_records_feature "
        "RENAME TO ix_llm_usage_records_feature"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_vendor_usage_records_model RENAME TO ix_llm_usage_records_model"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_vendor_usage_records_provider "
        "RENAME TO ix_llm_usage_records_provider"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_vendor_usage_records_created_at "
        "RENAME TO ix_llm_usage_records_created_at"
    )

    op.rename_table("vendor_usage_records", "llm_usage_records")
