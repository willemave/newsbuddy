# ruff: noqa: F401
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.constants import TWEET_SUGGESTION_MODEL
from app.models.api.pagination import PaginationMetadata
from app.models.contracts import (
    ContentClassification,
    ContentStatus,
    ContentType,
    DiscussionMode,
    SummaryKind,
    SummaryVersion,
)


class DiscussionLinkResponse(BaseModel):
    """Link extracted from comments or discussion lists."""

    url: str
    source: str = "unknown"
    comment_id: str | None = None
    group_label: str | None = None
    title: str | None = None


class DiscussionCommentResponse(BaseModel):
    """Normalized discussion comment."""

    comment_id: str
    parent_id: str | None = None
    author: str | None = None
    text: str
    compact_text: str | None = None
    depth: int = 0
    created_at: str | None = None
    source_url: str | None = None


class DiscussionItemResponse(BaseModel):
    """One discussion destination in a group (X, Forums, LinkedIn, etc.)."""

    title: str
    url: str


class DiscussionGroupResponse(BaseModel):
    """Grouped discussion destinations from Techmeme."""

    label: str
    items: list[DiscussionItemResponse] = Field(default_factory=list)


class DiscussionSummaryTopicResponse(BaseModel):
    """One high-signal theme surfaced by the discussion summarizer."""

    title: str
    summary: str
    stance: str | None = None


class DiscussionSummaryLinkResponse(BaseModel):
    """Interesting link surfaced by a discussion summary."""

    url: str
    title: str | None = None
    reason: str | None = None
    source_comment_id: str | None = None


class DiscussionSummaryCommentResponse(BaseModel):
    """Representative comment selected by the discussion summarizer."""

    comment_id: str | None = None
    author: str | None = None
    text: str
    reason: str | None = None


class DiscussionSummaryResponse(BaseModel):
    """Structured summary of a content item's external discussion.

    Mirrors ``app.models.metadata.summaries.DiscussionSummary``, which both
    discussion-payload producers (`get_content_discussion`,
    `get_news_item_discussion`) serialize via ``model_dump(mode="json")``.
    """

    overview: str
    topics: list[DiscussionSummaryTopicResponse] = Field(default_factory=list)
    notable_links: list[DiscussionSummaryLinkResponse] = Field(default_factory=list)
    representative_comments: list[DiscussionSummaryCommentResponse] = Field(default_factory=list)
    external_discussion_url: str | None = None
    generated_at: str | None = None


class ContentDiscussionResponse(BaseModel):
    """Discussion payload for a content item."""

    content_id: int
    status: str
    mode: DiscussionMode = DiscussionMode.NONE
    platform: str | None = None
    source_url: str | None = None
    discussion_url: str | None = None
    fetched_at: str | None = None
    error_message: str | None = None
    comments: list[DiscussionCommentResponse] = Field(default_factory=list)
    discussion_groups: list[DiscussionGroupResponse] = Field(default_factory=list)
    links: list[DiscussionLinkResponse] = Field(default_factory=list)
    summary: DiscussionSummaryResponse | None = None
    comment_count: int | None = None
    # Per-platform/per-mode fetch stats: Techmeme emits group_count/item_count;
    # Hacker News and Reddit emit cap/fetched_count/cap_reached/total_seen/
    # declared_comment_count (partial subsets on early exit); news-item discussions
    # emit a 9-key shape (comment_count, summary_status, summary_version, etc). No
    # iOS code reads a key out of this dict today. Genuinely heterogeneous across
    # producers, not just under-typed — kept as an intentional escape hatch rather
    # than a synthetic union type (see contracts_registry.py).
    stats: dict[str, Any] = Field(default_factory=dict)
