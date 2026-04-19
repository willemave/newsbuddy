"""Add news-native digest tables."""

import sqlalchemy as sa
from alembic import op

revision: str = "20260328_02"
down_revision: str | None = "20260328_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ingest_key", sa.String(length=128), nullable=False),
        sa.Column("visibility_scope", sa.String(length=20), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("platform", sa.String(length=50), nullable=True),
        sa.Column("source_type", sa.String(length=50), nullable=True),
        sa.Column("source_label", sa.String(length=255), nullable=True),
        sa.Column("source_external_id", sa.String(length=255), nullable=True),
        sa.Column("user_scraper_config_id", sa.Integer(), nullable=True),
        sa.Column("user_integration_connection_id", sa.Integer(), nullable=True),
        sa.Column("canonical_item_url", sa.String(length=2048), nullable=True),
        sa.Column("canonical_story_url", sa.String(length=2048), nullable=True),
        sa.Column("article_url", sa.String(length=2048), nullable=True),
        sa.Column("article_title", sa.String(length=500), nullable=True),
        sa.Column("article_domain", sa.String(length=255), nullable=True),
        sa.Column("discussion_url", sa.String(length=2048), nullable=True),
        sa.Column("summary_title", sa.String(length=240), nullable=True),
        sa.Column("summary_key_points", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("raw_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="new"),
        sa.Column("legacy_content_id", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ingest_key", name="uq_news_items_ingest_key"),
        sa.UniqueConstraint("legacy_content_id", name="uq_news_items_legacy_content_id"),
    )
    op.create_index("ix_news_items_ingest_key", "news_items", ["ingest_key"])
    op.create_index("ix_news_items_visibility_scope", "news_items", ["visibility_scope"])
    op.create_index("ix_news_items_owner_user_id", "news_items", ["owner_user_id"])
    op.create_index("ix_news_items_platform", "news_items", ["platform"])
    op.create_index("ix_news_items_source_type", "news_items", ["source_type"])
    op.create_index("ix_news_items_source_external_id", "news_items", ["source_external_id"])
    op.create_index(
        "ix_news_items_user_scraper_config_id", "news_items", ["user_scraper_config_id"]
    )
    op.create_index(
        "ix_news_items_user_integration_connection_id",
        "news_items",
        ["user_integration_connection_id"],
    )
    op.create_index("ix_news_items_canonical_story_url", "news_items", ["canonical_story_url"])
    op.create_index("ix_news_items_status", "news_items", ["status"])
    op.create_index("ix_news_items_legacy_content_id", "news_items", ["legacy_content_id"])
    op.create_index("ix_news_items_published_at", "news_items", ["published_at"])
    op.create_index("ix_news_items_ingested_at", "news_items", ["ingested_at"])
    op.create_index("ix_news_items_processed_at", "news_items", ["processed_at"])
    op.create_index(
        "idx_news_items_visibility_owner_status",
        "news_items",
        ["visibility_scope", "owner_user_id", "status"],
    )
    op.create_index("idx_news_items_status_ingested", "news_items", ["status", "ingested_at"])
    op.create_index(
        "idx_news_items_owner_ingested",
        "news_items",
        ["owner_user_id", "ingested_at"],
    )

    op.create_table(
        "news_digests",
        sa.Column("id", sa.Integer(), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_news_digests_user_id", "news_digests", ["user_id"])
    op.create_index("ix_news_digests_window_start_at", "news_digests", ["window_start_at"])
    op.create_index("ix_news_digests_window_end_at", "news_digests", ["window_end_at"])
    op.create_index("ix_news_digests_generated_at", "news_digests", ["generated_at"])
    op.create_index("ix_news_digests_read_at", "news_digests", ["read_at"])
    op.create_index(
        "idx_news_digests_user_generated",
        "news_digests",
        ["user_id", "generated_at"],
    )
    op.create_index("idx_news_digests_user_read", "news_digests", ["user_id", "read_at"])

    op.create_table(
        "news_digest_bullets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("digest_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=240), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest_id", "position", name="uq_news_digest_bullets_digest_position"),
    )
    op.create_index("ix_news_digest_bullets_digest_id", "news_digest_bullets", ["digest_id"])
    op.create_index(
        "idx_news_digest_bullets_digest",
        "news_digest_bullets",
        ["digest_id", "position"],
    )

    op.create_table(
        "news_digest_bullet_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bullet_id", sa.Integer(), nullable=False),
        sa.Column("news_item_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
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
        "ix_news_digest_bullet_sources_bullet_id", "news_digest_bullet_sources", ["bullet_id"]
    )
    op.create_index(
        "ix_news_digest_bullet_sources_news_item_id", "news_digest_bullet_sources", ["news_item_id"]
    )
    op.create_index(
        "idx_news_digest_bullet_sources_item",
        "news_digest_bullet_sources",
        ["news_item_id"],
    )

    op.create_table(
        "news_item_digest_coverage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("news_item_id", sa.Integer(), nullable=False),
        sa.Column("digest_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "news_item_id",
            name="uq_news_item_digest_coverage_user_item",
        ),
    )
    op.create_index(
        "ix_news_item_digest_coverage_user_id", "news_item_digest_coverage", ["user_id"]
    )
    op.create_index(
        "ix_news_item_digest_coverage_news_item_id",
        "news_item_digest_coverage",
        ["news_item_id"],
    )
    op.create_index(
        "ix_news_item_digest_coverage_digest_id", "news_item_digest_coverage", ["digest_id"]
    )
    op.create_index(
        "idx_news_item_digest_coverage_digest",
        "news_item_digest_coverage",
        ["digest_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_news_item_digest_coverage_digest", table_name="news_item_digest_coverage")
    op.drop_index("ix_news_item_digest_coverage_digest_id", table_name="news_item_digest_coverage")
    op.drop_index(
        "ix_news_item_digest_coverage_news_item_id",
        table_name="news_item_digest_coverage",
    )
    op.drop_index("ix_news_item_digest_coverage_user_id", table_name="news_item_digest_coverage")
    op.drop_table("news_item_digest_coverage")

    op.drop_index("idx_news_digest_bullet_sources_item", table_name="news_digest_bullet_sources")
    op.drop_index(
        "ix_news_digest_bullet_sources_news_item_id",
        table_name="news_digest_bullet_sources",
    )
    op.drop_index(
        "ix_news_digest_bullet_sources_bullet_id",
        table_name="news_digest_bullet_sources",
    )
    op.drop_table("news_digest_bullet_sources")

    op.drop_index("idx_news_digest_bullets_digest", table_name="news_digest_bullets")
    op.drop_index("ix_news_digest_bullets_digest_id", table_name="news_digest_bullets")
    op.drop_table("news_digest_bullets")

    op.drop_index("idx_news_digests_user_read", table_name="news_digests")
    op.drop_index("idx_news_digests_user_generated", table_name="news_digests")
    op.drop_index("ix_news_digests_read_at", table_name="news_digests")
    op.drop_index("ix_news_digests_generated_at", table_name="news_digests")
    op.drop_index("ix_news_digests_window_end_at", table_name="news_digests")
    op.drop_index("ix_news_digests_window_start_at", table_name="news_digests")
    op.drop_index("ix_news_digests_user_id", table_name="news_digests")
    op.drop_table("news_digests")

    op.drop_index("idx_news_items_owner_ingested", table_name="news_items")
    op.drop_index("idx_news_items_status_ingested", table_name="news_items")
    op.drop_index("idx_news_items_visibility_owner_status", table_name="news_items")
    op.drop_index("ix_news_items_processed_at", table_name="news_items")
    op.drop_index("ix_news_items_ingested_at", table_name="news_items")
    op.drop_index("ix_news_items_published_at", table_name="news_items")
    op.drop_index("ix_news_items_legacy_content_id", table_name="news_items")
    op.drop_index("ix_news_items_status", table_name="news_items")
    op.drop_index("ix_news_items_canonical_story_url", table_name="news_items")
    op.drop_index(
        "ix_news_items_user_integration_connection_id",
        table_name="news_items",
    )
    op.drop_index("ix_news_items_user_scraper_config_id", table_name="news_items")
    op.drop_index("ix_news_items_source_external_id", table_name="news_items")
    op.drop_index("ix_news_items_source_type", table_name="news_items")
    op.drop_index("ix_news_items_platform", table_name="news_items")
    op.drop_index("ix_news_items_owner_user_id", table_name="news_items")
    op.drop_index("ix_news_items_visibility_scope", table_name="news_items")
    op.drop_index("ix_news_items_ingest_key", table_name="news_items")
    op.drop_table("news_items")
