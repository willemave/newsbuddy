from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import ValidationError
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import validates

from app.core.db import Base
from app.core.logging import get_logger
from app.models.contracts import NewsItemStatus, NewsItemVisibilityScope
from app.models.metadata import (
    ArticleMetadata,
    ContentStatus,
    NewsMetadata,
    PodcastMetadata,
    StructuredSummary,
    validate_content_metadata,
)
from app.models.summary_contracts import is_structured_summary_payload
from app.models.user import User  # noqa: F401
from app.utils.news_titles import (
    get_news_article_title,
    get_news_summary_title,
    set_news_article_title,
    set_news_summary_title,
)
from app.utils.summary_utils import extract_short_summary

logger = get_logger(__name__)


def _utcnow() -> datetime:
    """Return a timezone-naive UTC timestamp for DB defaults."""
    return datetime.now(UTC).replace(tzinfo=None)


class Content(Base):
    __tablename__ = "contents"

    # Primary fields
    id = Column(Integer, primary_key=True)
    content_type = Column(String(20), nullable=False, index=True)
    url = Column(String(2048), nullable=False)
    source_url = Column(String(2048), nullable=True)
    title = Column(String(500), nullable=True)
    source = Column(String(100), nullable=True, index=True)
    platform = Column(String(50), nullable=True, index=True)
    is_aggregate = Column(Boolean, default=False, nullable=False, index=True)

    # Status tracking
    status = Column(String(20), default=ContentStatus.NEW.value, nullable=False, index=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)

    # Classification
    classification = Column(String(20), nullable=True, index=True)

    # Checkout mechanism
    checked_out_by = Column(String(100), nullable=True, index=True)
    checked_out_at = Column(DateTime, nullable=True)

    # Type-specific data stored as JSON
    # For articles: author, content, publish_date, source, internal_links
    # For podcasts: audio_url, transcript, duration, episode_number
    content_metadata = Column(JSON, default=dict, nullable=False)
    search_text = Column(Text, nullable=True)

    # Common timestamps
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    processed_at = Column(DateTime, nullable=True)
    publication_date = Column(DateTime, nullable=True, index=True)

    # Indexes for performance
    __table_args__ = (
        Index("idx_content_type_status", "content_type", "status"),
        Index("idx_checkout", "checked_out_by", "checked_out_at"),
        Index("idx_created_at", "created_at"),
        Index("idx_content_aggregate", "content_type", "is_aggregate"),
        Index("idx_url_content_type", "url", "content_type", unique=True),
        # Performance index for visibility queries (classification + status + content_type)
        Index("idx_contents_classification_status", "classification", "status", "content_type"),
    )

    @validates("content_metadata")
    def validate_metadata(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        """Validate metadata using Pydantic models."""
        if not value or value == {}:
            return value

        content_type = self.content_type
        if not isinstance(content_type, str) or not content_type:
            return value

        try:
            # Validate using appropriate schema
            validated = validate_content_metadata(content_type, value)
            # Convert back to dict for storage, excluding None values to preserve original data
            return validated.model_dump(mode="json", exclude_none=True)
        except ValidationError as e:
            logger.warning(f"Metadata validation failed for {content_type}: {e}")
            # For backward compatibility, store as-is but log warning
            return value
        except Exception as e:
            logger.error(f"Unexpected error validating metadata: {e}")
            return value

    def get_validated_metadata(self) -> ArticleMetadata | PodcastMetadata | NewsMetadata | None:
        """Get metadata as validated Pydantic model."""
        if not self.content_metadata:
            return None

        content_type = self.content_type
        if not isinstance(content_type, str) or not content_type:
            return None

        try:
            return validate_content_metadata(content_type, self.content_metadata)
        except Exception as e:
            logger.error(f"Error validating metadata for content {self.id}: {e}")
            return None

    def get_structured_summary(self) -> StructuredSummary | None:
        """Get structured summary if available."""
        if not self.content_metadata:
            return None

        summary = self.content_metadata.get("summary")
        summary_kind = self.content_metadata.get("summary_kind")
        if not summary:
            return None

        # Parse canonical structured summaries while preserving legacy payload tolerance.
        if isinstance(summary, dict) and is_structured_summary_payload(summary, summary_kind):
            try:
                return StructuredSummary(**summary)
            except Exception as e:
                logger.error(f"Error parsing structured summary: {e}")

        return None

    @property
    def short_summary(self) -> str | None:
        """Return a short summary for list views if available."""
        if not self.content_metadata:
            return None
        return extract_short_summary(self.content_metadata.get("summary"))


class ContentDiscussion(Base):
    """Discussion payload for a content item."""

    __tablename__ = "content_discussions"

    id = Column(Integer, primary_key=True)
    content_id = Column(Integer, nullable=False)
    platform = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    discussion_data = Column(JSON, default=dict, nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    fetched_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("content_id", name="uq_content_discussions_content"),
        Index("idx_content_discussions_platform", "platform"),
        Index("idx_content_discussions_status", "status"),
        Index("idx_content_discussions_fetched_at", "fetched_at"),
    )


class ContentBody(Base):
    """Canonical body pointer stored separately from `content_metadata`."""

    __tablename__ = "content_bodies"

    content_id = Column(Integer, primary_key=True)
    variant = Column(String(20), primary_key=True)
    storage_provider = Column(String(32), nullable=False)
    storage_bucket = Column(String(255), nullable=True)
    storage_key = Column(String(2048), nullable=False)
    content_format = Column(String(32), nullable=False)
    sha256 = Column(String(64), nullable=False)
    byte_size = Column(Integer, nullable=False, default=0)
    char_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("idx_content_bodies_content_id", "content_id"),
        Index("idx_content_bodies_storage_key", "storage_key"),
    )


class ProcessingTask(Base):
    """Simple task queue to replace Huey"""

    __tablename__ = "processing_tasks"

    id = Column(Integer, primary_key=True)
    task_type = Column(String(50), nullable=False, index=True)
    content_id = Column(Integer, nullable=True, index=True)
    payload = Column(JSON, default=dict)
    status = Column(String(20), default="pending", index=True)
    queue_name = Column(String(32), nullable=False, index=True, default="content")

    created_at = Column(DateTime, default=_utcnow)
    available_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    locked_at = Column(DateTime, nullable=True)
    locked_by = Column(String(100), nullable=True, index=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)

    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    dedupe_key = Column(String(512), nullable=True, index=True)

    __table_args__ = (
        Index("idx_task_status_created", "status", "created_at"),
        Index("idx_task_queue_status_created", "queue_name", "status", "created_at"),
        Index("idx_task_status_available", "status", "available_at", "retry_count", "id"),
        Index(
            "idx_task_queue_status_available",
            "queue_name",
            "status",
            "available_at",
            "retry_count",
            "created_at",
        ),
        Index(
            "uq_processing_tasks_dedupe_key_active",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL AND status IN ('pending', 'processing')"),
        ),
    )


class ContentReadStatus(Base):
    """Track which content has been read by which user."""

    __tablename__ = "content_read_status"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    content_id = Column(Integer, nullable=False, index=True)
    read_at = Column(DateTime, default=_utcnow, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (Index("idx_content_read_user_content", "user_id", "content_id", unique=True),)


class ContentKnowledgeSave(Base):
    """Track which content has been saved to knowledge by which user."""

    __tablename__ = "content_knowledge_saves"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    content_id = Column(Integer, nullable=False, index=True)
    saved_at = Column(DateTime, default=_utcnow, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_content_knowledge_saves_user_content", "user_id", "content_id", unique=True),
    )


class NewsItem(Base):
    """Short-form news evidence item used by the news-native digest pipeline."""

    __tablename__ = "news_items"

    id = Column(Integer, primary_key=True)
    ingest_key = Column(String(128), nullable=False, index=True)
    visibility_scope = Column(
        String(20),
        nullable=False,
        default=NewsItemVisibilityScope.GLOBAL.value,
        index=True,
    )
    owner_user_id = Column(Integer, nullable=True, index=True)
    platform = Column(String(50), nullable=True, index=True)
    source_type = Column(String(50), nullable=True, index=True)
    source_label = Column(String(255), nullable=True)
    source_external_id = Column(String(255), nullable=True, index=True)
    user_scraper_config_id = Column(Integer, nullable=True, index=True)
    user_integration_connection_id = Column(Integer, nullable=True, index=True)
    canonical_item_url = Column(String(2048), nullable=True)
    canonical_story_url = Column(String(2048), nullable=True, index=True)
    article_url = Column(String(2048), nullable=True)
    article_domain = Column(String(255), nullable=True)
    discussion_url = Column(String(2048), nullable=True)
    summary_key_points = Column(JSON, default=list, nullable=False)
    summary_text = Column(Text, nullable=True)
    raw_metadata = Column(JSON, default=dict, nullable=False)
    status = Column(String(20), nullable=False, default=NewsItemStatus.NEW.value, index=True)
    legacy_content_id = Column(Integer, nullable=True, index=True)
    representative_news_item_id = Column(Integer, nullable=True, index=True)
    cluster_size = Column(Integer, nullable=False, default=1)
    enrichment_updated_at = Column(DateTime, nullable=True, index=True)
    published_at = Column(DateTime, nullable=True, index=True)
    ingested_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    processed_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("ingest_key", name="uq_news_items_ingest_key"),
        UniqueConstraint("legacy_content_id", name="uq_news_items_legacy_content_id"),
        Index(
            "idx_news_items_visibility_owner_status", "visibility_scope", "owner_user_id", "status"
        ),
        Index("idx_news_items_status_ingested", "status", "ingested_at"),
        Index("idx_news_items_owner_ingested", "owner_user_id", "ingested_at"),
        Index(
            "idx_news_items_visible_feed",
            "visibility_scope",
            "owner_user_id",
            "representative_news_item_id",
            "status",
            "ingested_at",
        ),
    )

    def __init__(self, **kwargs: Any) -> None:
        article_title = kwargs.pop("article_title", None)
        summary_title = kwargs.pop("summary_title", None)
        super().__init__(**kwargs)
        if article_title is not None and self.article_title is None:
            self.article_title = article_title
        if summary_title is not None and self.summary_title is None:
            self.summary_title = summary_title

    @property
    def article_title(self) -> str | None:
        return get_news_article_title(self.raw_metadata)

    @article_title.setter
    def article_title(self, value: Any) -> None:
        self.raw_metadata = set_news_article_title(self.raw_metadata, value)

    @property
    def summary_title(self) -> str | None:
        return get_news_summary_title(self.raw_metadata)

    @summary_title.setter
    def summary_title(self, value: Any) -> None:
        self.raw_metadata = set_news_summary_title(self.raw_metadata, value)


class NewsItemReadStatus(Base):
    """Track which visible news items have been read by which user."""

    __tablename__ = "news_item_read_status"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    news_item_id = Column(Integer, nullable=False, index=True)
    read_at = Column(DateTime, default=_utcnow, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_news_item_read_status_user_item", "user_id", "news_item_id", unique=True),
    )


class FeedDiscoveryRun(Base):
    """Track a feed discovery run for a user."""

    __tablename__ = "feed_discovery_runs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True, default="pending")
    direction_summary = Column(Text, nullable=True)
    seed_content_ids = Column(JSON, default=list, nullable=False)
    token_input = Column(Integer, nullable=True)
    token_output = Column(Integer, nullable=True)
    token_total = Column(Integer, nullable=True)
    token_usage = Column(JSON, nullable=True)
    duration_ms_total = Column(Float, nullable=True)
    duration_ms_direction = Column(Float, nullable=True)
    duration_ms_lane = Column(Float, nullable=True)
    duration_ms_candidate_extract = Column(Float, nullable=True)
    duration_ms_candidate_validate = Column(Float, nullable=True)
    duration_ms_persist = Column(Float, nullable=True)
    timing_json = Column("timing", JSON, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (Index("idx_feed_discovery_runs_user_created", "user_id", "created_at"),)


class FeedDiscoverySuggestion(Base):
    """Suggested feed/podcast/YouTube subscription from discovery."""

    __tablename__ = "feed_discovery_suggestions"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    suggestion_type = Column(String(50), nullable=False, index=True)
    site_url = Column(String(2048), nullable=True)
    feed_url = Column(String(2048), nullable=False)
    item_url = Column(String(2048), nullable=True)
    title = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    channel_id = Column(String(255), nullable=True)
    playlist_id = Column(String(255), nullable=True)
    rationale = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, index=True, default="new")
    config = Column(JSON, default=dict, nullable=False)
    metadata_json = Column("metadata", JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "feed_url", name="uq_feed_discovery_user_feed"),
        Index("idx_feed_discovery_suggestions_user_status", "user_id", "status"),
    )


class OnboardingDiscoveryRun(Base):
    """Track an onboarding discovery run for a user."""

    __tablename__ = "onboarding_discovery_runs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True, default="pending")
    topic_summary = Column(Text, nullable=True)
    inferred_topics = Column(JSON, default=list, nullable=False)
    lane_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (Index("idx_onboarding_discovery_runs_user_created", "user_id", "created_at"),)


class OnboardingDiscoveryLane(Base):
    """Track a single onboarding discovery lane."""

    __tablename__ = "onboarding_discovery_lanes"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, nullable=False, index=True)
    lane_name = Column(String(160), nullable=False)
    goal = Column(Text, nullable=True)
    target = Column(String(30), nullable=True)
    status = Column(String(20), nullable=False, index=True, default="queued")
    query_count = Column(Integer, nullable=False, default=0)
    completed_queries = Column(Integer, nullable=False, default=0)
    queries = Column(JSON, default=list, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (Index("idx_onboarding_discovery_lanes_run", "run_id"),)


class OnboardingDiscoverySuggestion(Base):
    """Suggested subscription discovered during onboarding."""

    __tablename__ = "onboarding_discovery_suggestions"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    suggestion_type = Column(String(50), nullable=False, index=True)
    site_url = Column(String(2048), nullable=True)
    feed_url = Column(String(2048), nullable=True)
    subreddit = Column(String(255), nullable=True)
    title = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    rationale = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, index=True, default="new")
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("idx_onboarding_discovery_suggestions_run", "run_id"),
        Index("idx_onboarding_discovery_suggestions_user_status", "user_id", "status"),
    )


class ContentUnlikes(Base):
    """Track which content has been unliked by which user."""

    __tablename__ = "content_unlikes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    content_id = Column(Integer, nullable=False, index=True)
    unliked_at = Column(DateTime, default=_utcnow, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_content_unlikes_user_content", "user_id", "content_id", unique=True),
    )


class AnalyticsInteraction(Base):
    """Track append-only user interactions for content analytics."""

    __tablename__ = "analytics_interactions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    content_id = Column(Integer, nullable=False, index=True)
    interaction_type = Column(String(32), nullable=False, index=True)
    interaction_id = Column(String(36), nullable=False)
    surface = Column(String(64), nullable=True)
    context_data = Column(JSON, default=dict, nullable=False)
    occurred_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "interaction_id",
            name="uq_analytics_interactions_user_interaction",
        ),
        Index(
            "idx_analytics_interactions_user_type_occurred",
            "user_id",
            "interaction_type",
            "occurred_at",
        ),
        Index(
            "idx_analytics_interactions_user_content_occurred",
            "user_id",
            "content_id",
            "occurred_at",
        ),
    )


class VendorUsageRecord(Base):
    """Persist per-call vendor usage and estimated cost."""

    __tablename__ = "vendor_usage_records"

    id = Column(Integer, primary_key=True)
    provider = Column(String(50), nullable=False, index=True)
    model = Column(String(255), nullable=False, index=True)
    feature = Column(String(100), nullable=False, index=True)
    operation = Column(String(100), nullable=False, index=True)
    source = Column(String(50), nullable=True, index=True)
    request_id = Column(String(100), nullable=True, index=True)
    task_id = Column(Integer, nullable=True, index=True)
    content_id = Column(Integer, nullable=True, index=True)
    session_id = Column(Integer, nullable=True, index=True)
    message_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    request_count = Column(Integer, nullable=True)
    resource_count = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=True)
    currency = Column(String(8), nullable=False, default="USD")
    pricing_version = Column(String(50), nullable=True)
    metadata_json = Column("metadata", JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("idx_vendor_usage_provider_model_created", "provider", "model", "created_at"),
        Index("idx_vendor_usage_content_created", "content_id", "created_at"),
        Index("idx_vendor_usage_session_created", "session_id", "created_at"),
        Index("idx_vendor_usage_task_created", "task_id", "created_at"),
    )


class ContentStatusEntry(Base):
    """Per-user status for content feed membership."""

    __tablename__ = "content_status"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    content_id = Column(Integer, nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True, default="inbox")
    created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "content_id", name="idx_content_status_user_content"),
        # Performance index for inbox lookups (user_id + status + content_id)
        Index("idx_content_status_user_status_content", "user_id", "status", "content_id"),
    )


class UserScraperConfig(Base):
    """Per-user scraper configuration for dynamic sources."""

    __tablename__ = "user_scraper_configs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    scraper_type = Column(String(50), nullable=False, index=True)
    display_name = Column(String(255), nullable=True)
    feed_url = Column(String(2048), nullable=True)
    config = Column(JSON, default=dict, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "scraper_type", "feed_url", name="uq_user_scraper_feed"),
        Index("idx_user_scraper_user_type", "user_id", "scraper_type"),
    )


class UserIntegrationConnection(Base):
    """OAuth/API connection metadata for external providers per user."""

    __tablename__ = "user_integration_connections"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    provider = Column(String(50), nullable=False, index=True)
    provider_user_id = Column(String(255), nullable=True)
    provider_username = Column(String(255), nullable=True)
    access_token_encrypted = Column(Text, nullable=True)
    refresh_token_encrypted = Column(Text, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)
    scopes = Column(JSON, default=list, nullable=True)
    connection_metadata = Column(JSON, default=dict, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_provider_connection"),
        UniqueConstraint("provider", "provider_user_id", name="uq_provider_provider_user"),
        Index("idx_user_integration_provider_active", "provider", "is_active"),
    )


class UserIntegrationSyncState(Base):
    """Provider sync cursor/state for a single user integration connection."""

    __tablename__ = "user_integration_sync_state"

    id = Column(Integer, primary_key=True)
    connection_id = Column(Integer, nullable=False, index=True)
    cursor = Column(String(1024), nullable=True)
    last_synced_item_id = Column(String(255), nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    last_status = Column(String(50), nullable=True)
    last_error = Column(Text, nullable=True)
    sync_metadata = Column(JSON, default=dict, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("connection_id", name="uq_user_integration_sync_connection"),
        Index("idx_user_integration_sync_last_synced", "last_synced_at"),
    )


class UserIntegrationSyncedItem(Base):
    """Per-connection ledger of externally synced items."""

    __tablename__ = "user_integration_synced_items"

    id = Column(Integer, primary_key=True)
    connection_id = Column(Integer, nullable=False, index=True)
    channel = Column(String(50), nullable=False, index=True)
    external_item_id = Column(String(255), nullable=False, index=True)
    content_id = Column(Integer, nullable=True, index=True)
    item_url = Column(String(2048), nullable=True)
    first_synced_at = Column(DateTime, default=_utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=_utcnow, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "channel",
            "external_item_id",
            name="uq_user_integration_synced_item",
        ),
        Index(
            "idx_user_integration_synced_item_lookup",
            "connection_id",
            "channel",
            "last_seen_at",
        ),
    )


class UserApiKey(Base):
    """API key for machine-to-machine access on behalf of a user."""

    __tablename__ = "user_api_keys"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    key_prefix = Column(String(64), nullable=False, index=True)
    key_hash = Column(String(128), nullable=False)
    created_by_admin_user_id = Column(Integer, nullable=True, index=True)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_user_api_keys_user_revoked", "user_id", "revoked_at"),
        Index("idx_user_api_keys_prefix_revoked", "key_prefix", "revoked_at"),
    )


class CliLinkSession(Base):
    """Short-lived QR approval session for linking one CLI install."""

    __tablename__ = "cli_link_sessions"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), nullable=False, unique=True, index=True)
    approve_token_hash = Column(String(128), nullable=False)
    poll_token_hash = Column(String(128), nullable=False)
    requested_device_name = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    approved_by_user_id = Column(Integer, nullable=True, index=True)
    user_api_key_id = Column(Integer, nullable=True, index=True)
    issued_api_key_plaintext = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    approved_at = Column(DateTime, nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (Index("idx_cli_link_sessions_status_expires", "status", "expires_at"),)


class ChatSession(Base):
    """Chat session for deep-dive conversations with articles/news."""

    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    content_id = Column(Integer, nullable=True, index=True)  # soft ref to contents.id
    parent_session_id = Column(Integer, nullable=True, index=True)
    title = Column(String(500), nullable=True)
    session_type = Column(String(50), nullable=True)  # article_brain, topic, ad_hoc
    topic = Column(String(500), nullable=True)
    context_snapshot = Column(Text, nullable=True)
    council_persona_id = Column(String(64), nullable=True, index=True)
    council_persona_name = Column(String(120), nullable=True)
    council_persona_prompt = Column(Text, nullable=True)
    council_mode = Column(Boolean, default=False, nullable=False)
    active_child_session_id = Column(Integer, nullable=True, index=True)
    branch_start_message_id = Column(Integer, nullable=True, index=True)
    council_message_id = Column(Integer, nullable=True, index=True)
    is_hidden_from_history = Column(Boolean, default=False, nullable=False)
    llm_model = Column(String(100), nullable=False, default="openai:gpt-5.4")
    llm_provider = Column(String(50), nullable=False, default="openai")
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    last_message_at = Column(DateTime, nullable=True, index=True)
    is_archived = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("idx_chat_sessions_user_time", "user_id", "last_message_at"),
        Index("idx_chat_sessions_content", "user_id", "content_id"),
        Index("idx_chat_sessions_parent_hidden", "parent_session_id", "is_hidden_from_history"),
    )


class MessageProcessingStatus(StrEnum):
    """Processing status for async chat messages."""

    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ChatMessage(Base):
    """Chat message history stored as pydantic-ai ModelMessage JSON."""

    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, nullable=False, index=True)  # soft ref to chat_sessions.id
    message_list = Column(Text, nullable=False)  # JSON from ModelMessagesTypeAdapter
    render_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    # Async processing fields
    status = Column(
        String(20),
        nullable=False,
        default=MessageProcessingStatus.COMPLETED.value,
        index=True,
    )
    error = Column(Text, nullable=True)  # Error message if status=failed

    __table_args__ = (Index("idx_chat_messages_session_created", "session_id", "created_at"),)
