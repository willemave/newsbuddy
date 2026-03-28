"""Canonical domain contracts and enums shared across backend surfaces."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class ContentType(StrEnum):
    """Supported content types in storage and API contracts."""

    ARTICLE = "article"
    PODCAST = "podcast"
    NEWS = "news"
    UNKNOWN = "unknown"


class ContentStatus(StrEnum):
    """Lifecycle statuses for content processing."""

    NEW = "new"
    PENDING = "pending"
    PROCESSING = "processing"
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
    ANALYZE_URL = "analyze_url"
    PROCESS_CONTENT = "process_content"
    DOWNLOAD_AUDIO = "download_audio"
    TRANSCRIBE = "transcribe"
    SUMMARIZE = "summarize"
    FETCH_DISCUSSION = "fetch_discussion"
    GENERATE_IMAGE = "generate_image"
    DISCOVER_FEEDS = "discover_feeds"
    ONBOARDING_DISCOVER = "onboarding_discover"
    DIG_DEEPER = "dig_deeper"
    SYNC_INTEGRATION = "sync_integration"
    GENERATE_DAILY_NEWS_DIGEST = "generate_daily_news_digest"


class TaskQueue(StrEnum):
    """Queue partitions used by workers."""

    CONTENT = "content"
    IMAGE = "image"
    TRANSCRIBE = "transcribe"
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
    SHORT_NEWS_DIGEST = "short_news_digest"


class SummaryVersion(IntEnum):
    """Supported summary schema versions."""

    V1 = 1
    V2 = 2
