from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
)

from app.models.metadata.base import BaseContentMetadata
from app.utils.title_utils import clean_title


class NewsArticleMetadata(BaseModel):
    """Details about the linked article for a news item."""

    url: HttpUrl = Field(..., description="Canonical article URL to summarize")
    title: str | None = Field(None, max_length=500)
    source_domain: str | None = Field(None, max_length=200)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        """Normalize noisy titles and enforce max length defensively."""
        return clean_title(value)


class NewsAggregatorMetadata(BaseModel):
    """Context about the upstream aggregator (HN, Techmeme, Twitter)."""

    name: str | None = Field(None, max_length=120)
    title: str | None = Field(None, max_length=500)
    external_id: str | None = Field(None, max_length=200)
    author: str | None = Field(None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        """Normalize noisy aggregator titles and drop placeholders."""
        return clean_title(value)


class NewsMetadata(BaseContentMetadata):
    """Metadata structure for single-link news content."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source": "example.com",
                "platform": "hackernews",
                "article": {
                    "url": "https://example.com/story",
                    "title": "Example Story",
                    "source_domain": "example.com",
                },
                "aggregator": {
                    "name": "Hacker News",
                    "external_id": "123",
                    "metadata": {"score": 420},
                },
                "discussion_url": "https://news.ycombinator.com/item?id=123",
                "summary_kind": "short_news",
                "summary_version": 1,
                "summary": {
                    "title": "Techmeme: OpenAI ships GPT-5 with native agents",
                    "article_url": "https://example.com/story",
                    "key_points": [
                        "OpenAI launches GPT-5 with native agent orchestration",
                        "Developers get first-party workflows that replace plug-ins",
                        "Initial rollout targets enterprise customers later expanding to prosumers",
                    ],
                    "summary": (
                        "OpenAI debuts GPT-5 with native multi-agent features and enterprise-first "
                        "rollout."
                    ),
                    "classification": "to_read",
                    "summarization_date": "2025-09-22T10:30:00Z",
                },
            }
        }
    )

    article: NewsArticleMetadata = Field(..., description="Primary article information")
    aggregator: NewsAggregatorMetadata | None = Field(
        None, description="Upstream aggregator context"
    )
    discussion_url: HttpUrl | None = Field(
        None, description="Aggregator discussion link (HN thread, tweet, etc.)"
    )
    discovery_time: datetime | None = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the item was discovered",
    )
    top_comment: dict[str, str] | None = Field(
        None, description="First non-bot discussion comment {author, text} for feed preview"
    )
    comment_count: int | None = Field(
        None, ge=0, description="Discussion comment count denormalized by discussion fetcher"
    )
    has_video: bool = False
    video_duration_ms: int | None = Field(None, ge=0)
    video_audio_path: str | None = Field(None, max_length=2000)
    video_transcript: str | None = None


# Processing result model retained from the legacy content domain layer
