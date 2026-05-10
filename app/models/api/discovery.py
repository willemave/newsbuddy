# ruff: noqa: F401
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.constants import TWEET_SUGGESTION_MODEL
from app.models.api.pagination import PaginationMetadata
from app.models.contracts import (
    ContentClassification,
    ContentStatus,
    ContentType,
    SummaryKind,
    SummaryVersion,
)


class DiscoverySuggestionResponse(BaseModel):
    """Suggested feed/podcast/YouTube subscription item."""

    id: int
    suggestion_type: str
    site_url: str | None = None
    feed_url: str
    item_url: str | None = None
    title: str | None = None
    description: str | None = None
    channel_id: str | None = None
    playlist_id: str | None = None
    rationale: str | None = None
    score: float | None = None
    status: str
    created_at: str


class DiscoverySuggestionsResponse(BaseModel):
    """Grouped discovery suggestions for the latest run."""

    run_id: int | None = None
    run_status: str | None = None
    run_created_at: str | None = None
    direction_summary: str | None = None
    feeds: list[DiscoverySuggestionResponse] = Field(default_factory=list)
    podcasts: list[DiscoverySuggestionResponse] = Field(default_factory=list)
    youtube: list[DiscoverySuggestionResponse] = Field(default_factory=list)


class DiscoveryRunSuggestions(BaseModel):
    """Discovery suggestions grouped by run."""

    run_id: int
    run_status: str
    run_created_at: str
    direction_summary: str | None = None
    feeds: list[DiscoverySuggestionResponse] = Field(default_factory=list)
    podcasts: list[DiscoverySuggestionResponse] = Field(default_factory=list)
    youtube: list[DiscoverySuggestionResponse] = Field(default_factory=list)


class DiscoveryHistoryResponse(BaseModel):
    """Discovery suggestions across multiple runs."""

    runs: list[DiscoveryRunSuggestions] = Field(default_factory=list)


class DiscoveryRefreshResponse(BaseModel):
    """Response for manual discovery refresh."""

    status: str
    task_id: int | None = None


class DiscoverySubscribeRequest(BaseModel):
    """Request to subscribe to discovery suggestions."""

    suggestion_ids: list[int] = Field(..., min_length=1)


class DiscoverySubscribeResponse(BaseModel):
    """Response for discovery subscription action."""

    subscribed: list[int] = Field(default_factory=list)
    skipped: list[int] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)


class DiscoveryAddItemRequest(BaseModel):
    """Request to add single items from discovery suggestions."""

    suggestion_ids: list[int] = Field(..., min_length=1)


class DiscoveryAddItemResponse(BaseModel):
    """Response for adding items from discovery suggestions."""

    created: list[int] = Field(default_factory=list)
    skipped: list[int] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)


class DiscoveryDismissRequest(BaseModel):
    """Request to dismiss discovery suggestions."""

    suggestion_ids: list[int] = Field(..., min_length=1)


class DiscoveryDismissResponse(BaseModel):
    """Response for discovery dismissal action."""

    dismissed: list[int] = Field(default_factory=list)
