# ruff: noqa: F401
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.constants import TWEET_SUGGESTION_MODEL
from app.models.api.base import UTCDateTime
from app.models.api.onboarding import OnboardingSelectedAggregator
from app.models.api.pagination import PaginationMetadata
from app.models.contracts import (
    ContentClassification,
    ContentStatus,
    ContentType,
    SummaryKind,
    SummaryVersion,
)


class AgentLibraryDocumentResponse(BaseModel):
    """One manifest entry for a personal markdown document."""

    relative_path: str
    content_id: int
    variant: Literal["source", "summary"]
    updated_at: UTCDateTime | None = None
    size_bytes: int
    checksum_sha256: str


class AgentLibraryManifestResponse(BaseModel):
    """Manifest of markdown documents available for CLI sync."""

    generated_at: UTCDateTime
    include_source: bool = True
    documents: list[AgentLibraryDocumentResponse]


class AgentLibraryFileResponse(BaseModel):
    """One markdown document payload for CLI sync download."""

    relative_path: str
    content_id: int
    variant: Literal["source", "summary"]
    updated_at: UTCDateTime | None = None
    checksum_sha256: str
    text: str


class AgentSearchRequest(BaseModel):
    """Machine-oriented external search request."""

    query: str = Field(..., min_length=2, max_length=200)
    limit: int = Field(default=10, ge=1, le=25)
    include_podcasts: bool = True


class AgentSearchResultResponse(BaseModel):
    """One agent search result."""

    kind: Literal["web", "podcast"]
    title: str
    url: str
    snippet: str | None = None
    source: str | None = None
    provider: str | None = None
    feed_url: str | None = None
    published_at: str | None = None
    score: float | None = None


class AgentSearchResponse(BaseModel):
    """Machine-oriented external search response."""

    results: list[AgentSearchResultResponse] = Field(default_factory=list)


class AgentOnboardingStartRequest(BaseModel):
    """Simplified async onboarding start request."""

    brief: str = Field(..., min_length=1, max_length=4000)
    preferences: dict[str, Any] | None = None
    seed_urls: list[str] = Field(default_factory=list)
    seed_feeds: list[str] = Field(default_factory=list)


class AgentOnboardingStartResponse(BaseModel):
    """Simplified async onboarding start response."""

    run_id: int
    status: str
    job_id: int | None = None


class AgentOnboardingCompleteRequest(BaseModel):
    """Complete simplified agent onboarding."""

    accept_all: bool = False
    source_ids: list[int] = Field(default_factory=list)
    selected_subreddits: list[str] = Field(default_factory=list)
    selected_aggregators: list[OnboardingSelectedAggregator] = Field(default_factory=list)
