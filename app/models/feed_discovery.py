"""Pydantic models for feed discovery workflow."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FavoriteSnapshot(BaseModel):
    """Compact representation of a favorited content item."""

    id: int
    title: str | None = None
    source: str | None = None
    url: str
    content_type: str
    summary: str | None = None


class DiscoveryDirection(BaseModel):
    """A thematic direction for discovery exploration."""

    name: str = Field(..., min_length=2, max_length=120)
    rationale: str = Field(..., min_length=10)
    favorite_ids: list[int] = Field(min_length=1)


class DiscoveryDirectionPlan(BaseModel):
    """Plan containing directions to explore."""

    summary: str | None = None
    directions: list[DiscoveryDirection] = Field(min_length=2, max_length=4)


class DiscoveryQuery(BaseModel):
    """Single search query for a discovery lane."""

    query: str = Field(..., min_length=3, max_length=200)
    rationale: str = Field(..., min_length=5, max_length=300)


class DiscoveryLane(BaseModel):
    """Lane specification for a discovery run."""

    name: str = Field(..., min_length=2, max_length=80)
    goal: str = Field(..., min_length=10, max_length=300)
    target: Literal["feeds", "podcasts", "youtube"]
    queries: list[DiscoveryQuery] = Field(min_length=2, max_length=4)


class DiscoveryLanePlan(BaseModel):
    """Plan containing multiple discovery lanes."""

    lanes: list[DiscoveryLane] = Field(min_length=3, max_length=6)


class DiscoveryCandidate(BaseModel):
    """Candidate source discovered from search results."""

    title: str | None = None
    description: str | None = None
    site_url: str
    feed_url: str | None = None
    item_url: str | None = None
    suggestion_type: Literal["atom", "substack", "podcast_rss", "youtube"] | None = None
    channel_id: str | None = None
    playlist_id: str | None = None
    rationale: str = Field(..., min_length=10)
    evidence_urls: list[str] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0, le=1)
    config: dict[str, object] | None = None


class DiscoveryCandidateBatch(BaseModel):
    """Batch of candidates from a lane."""

    candidates: list[DiscoveryCandidate] = Field(min_length=1, max_length=20)


class DiscoveryRunResult(BaseModel):
    """Result summary for a feed discovery run."""

    run_id: int
    feeds: int
    podcasts: int
    youtube: int
    status: str
