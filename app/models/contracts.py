"""Canonical domain contracts and enums shared across backend surfaces."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class ContentType(StrEnum):
    """Supported content types in storage and API contracts."""

    ARTICLE = "article"
    PODCAST = "podcast"
    NEWS = "news"
    INSIGHT_REPORT = "insight_report"
    UNKNOWN = "unknown"


class ContentStatus(StrEnum):
    """Lifecycle statuses for content processing."""

    NEW = "new"
    PENDING = "pending"
    PROCESSING = "processing"
    AWAITING_IMAGE = "awaiting_image"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ContentClassification(StrEnum):
    """User-visible read prioritization classification."""

    TO_READ = "to_read"
    SKIP = "skip"


class TaskType(StrEnum):
    """Queue task types."""

    SCRAPE = "scrape"
    BACKFILL_FEEDS = "backfill_feeds"
    ANALYZE_URL = "analyze_url"
    PROCESS_CONTENT = "process_content"
    ENRICH_NEWS_ITEM_ARTICLE = "enrich_news_item_article"
    PROCESS_NEWS_ITEM = "process_news_item"
    PROCESS_PODCAST_MEDIA = "process_podcast_media"
    DOWNLOAD_AUDIO = "download_audio"
    TRANSCRIBE = "transcribe"
    SUMMARIZE = "summarize"
    FETCH_DISCUSSION = "fetch_discussion"
    GENERATE_IMAGE = "generate_image"
    DISCOVER_FEEDS = "discover_feeds"
    GENERATE_AGENT_DIGEST = "generate_agent_digest"
    ONBOARDING_DISCOVER = "onboarding_discover"
    DIG_DEEPER = "dig_deeper"
    SYNC_INTEGRATION = "sync_integration"
    GENERATE_INSIGHT_REPORT = "generate_insight_report"


class TaskQueue(StrEnum):
    """Queue partitions used by workers."""

    CONTENT = "content"
    MEDIA = "media"
    IMAGE = "image"
    ONBOARDING = "onboarding"
    TWITTER = "twitter"
    CHAT = "chat"


class TaskStatus(StrEnum):
    """Task execution status values."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class SummaryKind(StrEnum):
    """Canonical summary kind discriminators."""

    LONG_STRUCTURED = "long_structured"
    LONG_INTERLEAVED = "long_interleaved"
    LONG_BULLETS = "long_bullets"
    LONG_EDITORIAL_NARRATIVE = "long_editorial_narrative"
    SHORT_NEWS = "short_news"


class SummaryVersion(IntEnum):
    """Supported summary schema versions."""

    V1 = 1
    V2 = 2


class NewsItemVisibilityScope(StrEnum):
    """Audience visibility for one short-form news item."""

    GLOBAL = "global"
    USER = "user"


class NewsItemStatus(StrEnum):
    """Lifecycle status for short-form news items."""

    NEW = "new"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
