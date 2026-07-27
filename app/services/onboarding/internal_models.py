"""Internal typed values shared by onboarding services."""

from typing import Literal

from pydantic import BaseModel, Field

SuggestionType = Literal["substack", "atom", "podcast_rss", "reddit"]


class _ProfileOutput(BaseModel):
    """LLM output for onboarding profile creation."""

    profile_summary: str
    inferred_topics: list[str] = Field(default_factory=list)
    candidate_sources: list[str] = Field(default_factory=list)


class _DiscoverSuggestion(BaseModel):
    """LLM output suggestion seed."""

    title: str | None = None
    site_url: str | None = None
    feed_url: str | None = None
    candidate_feed_url: str | None = None
    is_likely_feed: bool | None = None
    feed_confidence: float | None = Field(default=None, ge=0, le=1)
    subreddit: str | None = None
    rationale: str | None = None
    score: float | None = None


class _DiscoverOutput(BaseModel):
    """LLM output for onboarding discovery."""

    substacks: list[_DiscoverSuggestion] = Field(default_factory=list)
    podcasts: list[_DiscoverSuggestion] = Field(default_factory=list)
    subreddits: list[_DiscoverSuggestion] = Field(default_factory=list)


class _DiscoveryWebResult(BaseModel):
    """Web result used for onboarding discovery prompting."""

    title: str
    url: str
    snippet: str | None = None
    published_date: str | None = None
    query: str | None = None
    lane_name: str | None = None
    lane_target: Literal["feeds", "podcasts", "reddit"] | None = None


class _VoiceParseOutput(BaseModel):
    """LLM output for onboarding voice parsing."""

    first_name: str | None = None
    interest_topics: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)


class _AudioLane(BaseModel):
    """LLM output for a single onboarding discovery lane."""

    name: str
    goal: str
    target: Literal["feeds", "podcasts", "reddit"]
    queries: list[str] = Field(default_factory=list)


class _AudioPlanOutput(BaseModel):
    """LLM output for onboarding audio discovery planning."""

    topic_summary: str
    inferred_topics: list[str] = Field(default_factory=list)
    lanes: list[_AudioLane] = Field(default_factory=list)
