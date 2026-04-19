"""Deprecate news digests and promote news items to the short-form feed."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260401_01"
down_revision = "20260330_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "news_digest_preference_prompt",
        new_column_name="news_list_preference_prompt",
    )
    op.drop_column("users", "news_digest_interval_hours")
    op.drop_column("users", "news_digest_timezone")

    op.add_column(
        "news_items",
        sa.Column("representative_news_item_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "news_items",
        sa.Column("cluster_size", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "news_items",
        sa.Column("enrichment_updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_news_items_visible_feed",
        "news_items",
        [
            "visibility_scope",
            "owner_user_id",
            "representative_news_item_id",
            "status",
            "ingested_at",
        ],
    )

    op.create_table(
        "news_item_read_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("news_item_id", sa.Integer(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "idx_news_item_read_status_user_item",
        "news_item_read_status",
        ["user_id", "news_item_id"],
        unique=True,
    )
    op.create_index(
        "idx_news_item_read_status_user_id",
        "news_item_read_status",
        ["user_id"],
    )
    op.create_index(
        "idx_news_item_read_status_news_item_id",
        "news_item_read_status",
        ["news_item_id"],
    )

    op.execute(
        """
        INSERT INTO news_item_read_status (user_id, news_item_id, read_at, created_at)
        SELECT crs.user_id, ni.id, crs.read_at, crs.created_at
        FROM content_read_status AS crs
        JOIN news_items AS ni ON ni.legacy_content_id = crs.content_id
        WHERE ni.representative_news_item_id IS NULL
        """
    )

    op.drop_table("news_digest_bullet_sources")
    op.drop_table("news_digest_bullets")
    op.drop_table("news_item_digest_coverage")
    op.drop_table("news_digests")


def downgrade() -> None:
    op.create_table(
        "news_digests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("window_start_at", sa.DateTime(), nullable=False),
        sa.Column("window_end_at", sa.DateTime(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("group_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_model", sa.String(length=255), nullable=False),
        sa.Column("llm_model", sa.String(length=255), nullable=False),
        sa.Column("pipeline_version", sa.String(length=64), nullable=False),
        sa.Column("trigger_reason", sa.String(length=64), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("build_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_news_digests_user_generated", "news_digests", ["user_id", "generated_at"])
    op.create_index("idx_news_digests_user_read", "news_digests", ["user_id", "read_at"])

    op.create_table(
        "news_digest_bullets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("digest_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=240), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("digest_id", "position", name="uq_news_digest_bullets_digest_position"),
    )
    op.create_index(
        "idx_news_digest_bullets_digest",
        "news_digest_bullets",
        ["digest_id", "position"],
    )

    op.create_table(
        "news_digest_bullet_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bullet_id", sa.Integer(), nullable=False),
        sa.Column("news_item_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "bullet_id",
            "news_item_id",
            name="uq_news_digest_bullet_sources_bullet_item",
        ),
        sa.UniqueConstraint(
            "bullet_id",
            "position",
            name="uq_news_digest_bullet_sources_bullet_position",
        ),
    )
    op.create_index(
        "idx_news_digest_bullet_sources_item",
        "news_digest_bullet_sources",
        ["news_item_id"],
    )

    op.create_table(
        "news_item_digest_coverage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("news_item_id", sa.Integer(), nullable=False),
        sa.Column("digest_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "user_id", "news_item_id", name="uq_news_item_digest_coverage_user_item"
        ),
    )
    op.create_index(
        "idx_news_item_digest_coverage_digest",
        "news_item_digest_coverage",
        ["digest_id"],
    )

    op.drop_index("idx_news_item_read_status_news_item_id", table_name="news_item_read_status")
    op.drop_index("idx_news_item_read_status_user_id", table_name="news_item_read_status")
    op.drop_index("idx_news_item_read_status_user_item", table_name="news_item_read_status")
    op.drop_table("news_item_read_status")

    op.drop_index("idx_news_items_visible_feed", table_name="news_items")
    op.drop_column("news_items", "enrichment_updated_at")
    op.drop_column("news_items", "cluster_size")
    op.drop_column("news_items", "representative_news_item_id")

    op.add_column(
        "users",
        sa.Column(
            "news_digest_timezone", sa.String(length=100), nullable=False, server_default="UTC"
        ),
    )
    op.add_column(
        "users",
        sa.Column("news_digest_interval_hours", sa.Integer(), nullable=False, server_default="6"),
    )
    op.alter_column(
        "users",
        "news_list_preference_prompt",
        new_column_name="news_digest_preference_prompt",
    )
