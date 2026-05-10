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


class OnboardingProfileRequest(BaseModel):
    """Request to build a profile for onboarding personalization."""

    first_name: str = Field(..., min_length=1, max_length=120)
    interest_topics: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_interest_topics(self) -> OnboardingProfileRequest:
        cleaned: list[str] = []
        seen: set[str] = set()
        for topic in self.interest_topics:
            if not isinstance(topic, str):
                continue
            normalized = topic.strip().strip(".,;:")
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(normalized)
        if not cleaned:
            raise ValueError("interest_topics is required")
        self.interest_topics = cleaned
        return self


class OnboardingProfileResponse(BaseModel):
    """Profile summary for onboarding personalization."""

    profile_summary: str
    inferred_topics: list[str] = Field(default_factory=list)
    candidate_sources: list[str] = Field(default_factory=list)


class OnboardingVoiceParseRequest(BaseModel):
    """Request to parse onboarding voice transcript into fields."""

    transcript: str = Field(..., min_length=3, max_length=6000)
    locale: str | None = Field(None, max_length=20)


class OnboardingVoiceParseResponse(BaseModel):
    """Parsed onboarding voice fields."""

    first_name: str | None = None
    interest_topics: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    missing_fields: list[str] = Field(default_factory=list)


class OnboardingAudioDiscoverRequest(BaseModel):
    """Request to start onboarding audio discovery."""

    transcript: str = Field(..., min_length=3, max_length=8000)
    locale: str | None = Field(None, max_length=20)


class OnboardingDiscoveryLaneStatus(BaseModel):
    """Status for a single onboarding discovery lane."""

    name: str
    status: str
    completed_queries: int = 0
    query_count: int = 0


class OnboardingAudioLanePreview(BaseModel):
    """Preview payload for a generated onboarding discovery lane."""

    name: str
    goal: str
    target: Literal["feeds", "podcasts", "reddit"]
    queries: list[str] = Field(default_factory=list)
    include_social: bool = False
    exa_results_per_query: int = 0


class OnboardingAudioLanePreviewResponse(BaseModel):
    """Preview response for onboarding audio lane generation."""

    topic_summary: str
    inferred_topics: list[str] = Field(default_factory=list)
    lanes: list[OnboardingAudioLanePreview] = Field(default_factory=list)
    used_fallback: bool = False
    fallback_reason: str | None = None


class OnboardingAudioDiscoverResponse(BaseModel):
    """Response for onboarding audio discovery start."""

    run_id: int
    run_status: str
    topic_summary: str | None = None
    inferred_topics: list[str] = Field(default_factory=list)
    lanes: list[OnboardingDiscoveryLaneStatus] = Field(default_factory=list)


class OnboardingSuggestion(BaseModel):
    """Single onboarding recommendation item."""

    suggestion_type: Literal["substack", "atom", "podcast_rss", "reddit"]
    title: str | None = None
    site_url: str | None = None
    feed_url: str | None = None
    subreddit: str | None = None
    rationale: str | None = None
    score: float | None = None
    is_default: bool = False


class OnboardingFastDiscoverRequest(BaseModel):
    """Request for fast onboarding discovery."""

    profile_summary: str = Field(..., min_length=3)
    inferred_topics: list[str] = Field(default_factory=list, max_length=12)


class OnboardingFastDiscoverResponse(BaseModel):
    """Response for fast onboarding discovery."""

    recommended_pods: list[OnboardingSuggestion] = Field(default_factory=list)
    recommended_substacks: list[OnboardingSuggestion] = Field(default_factory=list)
    recommended_subreddits: list[OnboardingSuggestion] = Field(default_factory=list)


class OnboardingDiscoveryStatusResponse(BaseModel):
    """Status response for onboarding audio discovery polling."""

    run_id: int
    run_status: str
    topic_summary: str | None = None
    inferred_topics: list[str] = Field(default_factory=list)
    lanes: list[OnboardingDiscoveryLaneStatus] = Field(default_factory=list)
    suggestions: OnboardingFastDiscoverResponse | None = None
    error_message: str | None = None


class OnboardingSelectedSource(BaseModel):
    """Selected source for onboarding completion."""

    suggestion_type: Literal["substack", "atom", "podcast_rss"]
    title: str | None = None
    feed_url: str = Field(..., min_length=5, max_length=2048)
    config: dict[str, Any] | None = None


class OnboardingSelectedAggregator(BaseModel):
    """Fast-news aggregator the user picked during onboarding."""

    key: str = Field(..., min_length=1, max_length=64)
    title: str | None = None
    # Optional per-aggregator topic picks (currently only Brutalist Report uses
    # this — other aggregators ignore it).
    topics: list[str] = Field(default_factory=list)


class OnboardingCompleteRequest(BaseModel):
    """Request to finalize onboarding selections."""

    selected_sources: list[OnboardingSelectedSource] = Field(default_factory=list)
    selected_subreddits: list[str] = Field(default_factory=list)
    selected_aggregators: list[OnboardingSelectedAggregator] = Field(default_factory=list)
    profile_summary: str | None = None
    inferred_topics: list[str] | None = None
    twitter_username: str | None = Field(default=None, max_length=50)


class OnboardingCompleteResponse(BaseModel):
    """Response for onboarding completion."""

    status: str
    task_id: int | None = None
    inbox_count_estimate: int
    configured_source_count: int
    longform_status: str
    has_completed_onboarding: bool
    has_completed_new_user_tutorial: bool


class OnboardingTutorialResponse(BaseModel):
    """Response for tutorial completion."""

    has_completed_new_user_tutorial: bool
