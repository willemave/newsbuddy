"""Structured metadata attached to chat messages for client rendering."""

from __future__ import annotations

from hashlib import sha1
from typing import Literal

from pydantic import BaseModel, Field, field_validator

FeedType = Literal["atom", "substack", "podcast_rss"]
FeedFormat = Literal["rss", "atom"]


class AssistantFeedOption(BaseModel):
    """Validated feed option surfaced on an assistant message."""

    id: str = Field(..., min_length=8, max_length=40)
    title: str = Field(..., min_length=1, max_length=300)
    site_url: str = Field(..., min_length=1, max_length=2048)
    feed_url: str = Field(..., min_length=1, max_length=2048)
    feed_type: FeedType
    feed_format: FeedFormat = "rss"
    description: str | None = Field(default=None, max_length=600)
    rationale: str | None = Field(default=None, max_length=600)
    evidence_url: str | None = Field(default=None, max_length=2048)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> object:
        """Trim titles before validation."""

        if isinstance(value, str):
            return value.strip()
        return value


class CouncilCandidate(BaseModel):
    """One council reply candidate rendered inside the parent transcript."""

    persona_id: str = Field(..., min_length=1, max_length=50)
    persona_name: str = Field(..., min_length=1, max_length=80)
    child_session_id: int = Field(..., ge=1)
    content: str = Field(..., min_length=1)
    status: str = Field(default="completed", min_length=1, max_length=32)
    order: int = Field(..., ge=0, le=3)


class ChatMessageRenderMetadata(BaseModel):
    """Structured UI metadata stored alongside a chat message."""

    feed_options: list[AssistantFeedOption] = Field(default_factory=list)
    council_candidates: list[CouncilCandidate] = Field(default_factory=list)
    active_council_child_session_id: int | None = Field(default=None, ge=1)


class AssistantFeedOptionsResult(BaseModel):
    """Tool-return payload for assistant feed-finder calls."""

    query: str = Field(..., min_length=1, max_length=300)
    options: list[AssistantFeedOption] = Field(default_factory=list)


def build_assistant_feed_option_id(feed_url: str) -> str:
    """Build a stable option ID from a normalized feed URL."""

    return sha1(feed_url.encode("utf-8")).hexdigest()[:16]
