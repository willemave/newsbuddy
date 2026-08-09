"""API schemas for scraper configuration endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.api.base import UTCDateTime
from app.models.contracts import FeedSubscriptionOutcome


class ScraperConfigStatsResponse(BaseModel):
    """Derived stats for a single scraper configuration."""

    total_count: int
    completed_count: int
    unread_count: int
    processing_count: int
    latest_processed_at: UTCDateTime | None = None
    latest_publication_at: UTCDateTime | None = None
    next_expected_at: UTCDateTime | None = None
    average_interval_hours: float | None = None
    interval_sample_size: int = 0


class ScraperConfigResponse(BaseModel):
    """Response model for scraper configs."""

    id: int
    scraper_type: str
    display_name: str | None = None
    config: dict[str, Any]
    feed_url: str | None = None
    limit: int | None = None
    is_active: bool
    created_at: UTCDateTime
    stats: ScraperConfigStatsResponse | None = None
    subscription_outcome: FeedSubscriptionOutcome | None = None
    backfill_task_id: int | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "scraper_type": "substack",
                "display_name": "Import AI",
                "config": {"feed_url": "https://example.substack.com/feed"},
                "feed_url": "https://example.substack.com/feed",
                "limit": 10,
                "is_active": True,
                "created_at": "2025-06-24T12:00:00Z",
                "stats": {
                    "total_count": 8,
                    "completed_count": 7,
                    "unread_count": 3,
                    "processing_count": 1,
                    "latest_processed_at": "2026-03-30T21:30:00Z",
                    "latest_publication_at": "2026-03-30T20:00:00Z",
                    "next_expected_at": "2026-04-01T08:00:00Z",
                    "average_interval_hours": 24.0,
                    "interval_sample_size": 3,
                },
            }
        }
    )


class SubscribeToFeedRequest(BaseModel):
    """Request to subscribe to a detected feed."""

    feed_url: str
    feed_type: str
    display_name: str | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "feed_url": "https://example.substack.com/feed",
                "feed_type": "substack",
                "display_name": "Example Newsletter",
            }
        }
    )
