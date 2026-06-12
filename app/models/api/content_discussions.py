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
    summary: dict[str, Any] | None = None
    comment_count: int | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
