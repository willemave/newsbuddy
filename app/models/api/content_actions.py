# ruff: noqa: F401
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.constants import TWEET_SUGGESTION_MODEL
from app.models.api.pagination import PaginationMetadata
from app.models.contracts import (
    ContentClassification,
    ContentStatus,
    ContentType,
    KnowledgeMutationStatus,
    OperationStatus,
    SummaryKind,
    SummaryVersion,
    TweetLength,
)


class DownloadMoreRequest(BaseModel):
    """Request to download older items from the same feed series."""

    count: int = Field(
        ...,
        ge=1,
        le=50,
        description="Number of additional older items to attempt to fetch",
    )


class DownloadMoreResponse(BaseModel):
    """Response for the download-more action."""

    status: str = Field(..., description="Completion status")
    requested_count: int = Field(..., ge=1, le=50)
    base_limit: int = Field(..., ge=1)
    target_limit: int = Field(..., ge=1)
    scraped: int = Field(..., ge=0)
    saved: int = Field(..., ge=0)
    duplicates: int = Field(..., ge=0)
    errors: int = Field(..., ge=0)


class BulkMarkReadRequest(BaseModel):
    """Request to mark multiple content items as read."""

    content_ids: list[int] = Field(
        ..., description="List of content IDs to mark as read", min_length=1
    )

    model_config = ConfigDict(json_schema_extra={"example": {"content_ids": [123, 456, 789]}})


class MarkReadResponse(BaseModel):
    """Response for marking one content item as read."""

    status: OperationStatus = OperationStatus.SUCCESS
    content_id: int = Field(..., description="ID of the content item marked as read")


class MarkUnreadResponse(BaseModel):
    """Response for marking one content item as unread."""

    status: OperationStatus = OperationStatus.SUCCESS
    content_id: int = Field(..., description="ID of the content item marked as unread")
    removed_records: int = Field(..., ge=0, description="Read-status rows removed")


class BulkMarkReadResponse(BaseModel):
    """Response for bulk read-status updates."""

    status: OperationStatus = OperationStatus.SUCCESS
    marked_count: int = Field(..., ge=0)
    failed_ids: list[int] = Field(default_factory=list)
    total_requested: int = Field(..., ge=0)


class KnowledgeMutationResponse(BaseModel):
    """Response for Knowledge save/remove actions."""

    status: KnowledgeMutationStatus
    content_id: int = Field(..., description="ID of the content item")
    is_saved_to_knowledge: bool
    message: str


class ChatGPTUrlResponse(BaseModel):
    """Response containing the ChatGPT URL for chatting with content."""

    chat_url: str = Field(..., description="URL to open ChatGPT with the content")
    truncated: bool = Field(..., description="Whether the content was truncated to fit URL limits")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "chat_url": "https://chat.openai.com/?q=Chat+about+this+article...",
                "truncated": False,
            }
        }
    )


class UnreadCountsResponse(BaseModel):
    """Response containing unread counts by content type."""

    article: int = Field(..., description="Number of unread articles")
    podcast: int = Field(..., description="Number of unread podcasts")
    news: int = Field(..., description="Number of unread news items")


class ProcessingCountResponse(BaseModel):
    """Response containing processing counts grouped by lane."""

    processing_count: int = Field(
        ...,
        description="Total number of inbox items queued, pending, or processing for the user",
    )
    long_form_count: int = Field(
        ...,
        description="Number of long-form inbox items queued, pending, or processing",
    )
    news_count: int = Field(
        ...,
        description="Number of short-form news inbox items queued, pending, or processing",
    )
    news_crawl_count: int = Field(
        ...,
        description="Number of selected short-form news sources currently being crawled",
    )


class BadgeStatsResponse(BaseModel):
    """Combined stats used for app badge refreshes."""

    unread: UnreadCountsResponse
    processing: ProcessingCountResponse


class LongFormStatsResponse(BaseModel):
    """Response containing unread long-form count for a user."""

    unread_count: int = Field(..., description="Unread long-form items")


class ConvertNewsResponse(BaseModel):
    """Response for converting news link to article."""

    status: str = Field(..., description="Operation status")
    new_content_id: int = Field(..., description="ID of the article content")
    original_content_id: int = Field(..., description="ID of the original news content")
    already_exists: bool = Field(..., description="Whether article already existed")
    message: str = Field(..., description="Human-readable message")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "new_content_id": 123,
                "original_content_id": 456,
                "already_exists": False,
                "message": "Article created and queued for processing",
            }
        }
    )


class TweetSuggestion(BaseModel):
    """A single tweet suggestion generated by the LLM."""

    id: int = Field(..., ge=1, le=3, description="Suggestion ID (1-3)")
    text: str = Field(..., description="Tweet text")
    style_label: str | None = Field(
        None, description="Style descriptor (e.g., 'insightful', 'provocative')"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "text": (
                    "Great read on AI agents. Key insight: the best agents don't try "
                    "to be human, they try to be useful. https://example.com/article"
                ),
                "style_label": "insightful",
            }
        }
    )


class TweetSuggestionsRequest(BaseModel):
    """Request body for generating tweet suggestions."""

    message: str | None = Field(
        None,
        max_length=500,
        description="Optional user guidance for tweet generation",
    )
    creativity: int = Field(
        5,
        ge=1,
        le=10,
        description="Creativity level 1-10 (1=factual, 10=bold/playful)",
    )
    length: TweetLength = Field(
        TweetLength.MEDIUM,
        description="Tweet length preference (short=100-180, medium=180-280, long=280-400 chars)",
    )
    llm_provider: str | None = Field(
        None,
        description="LLM provider to use (openai, anthropic, google). Defaults to google.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "emphasize the startup angle",
                "creativity": 7,
                "length": "medium",
                "llm_provider": "google",
            }
        }
    )


class TweetSuggestionsResponse(BaseModel):
    """Response containing generated tweet suggestions."""

    content_id: int = Field(..., description="ID of the content these tweets are about")
    creativity: int = Field(..., description="Creativity level used for generation")
    length: TweetLength = Field(..., description="Length preference used for generation")
    model: str = Field(
        default=TWEET_SUGGESTION_MODEL,
        description="LLM model used for generation",
    )
    suggestions: list[TweetSuggestion] = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Exactly 3 tweet suggestions",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content_id": 123,
                "creativity": 7,
                "model": TWEET_SUGGESTION_MODEL,
                "suggestions": [
                    {
                        "id": 1,
                        "text": (
                            "Great read on AI agents. The best agents don't try to be "
                            "human, they try to be useful. https://example.com"
                        ),
                        "style_label": "insightful",
                    },
                    {
                        "id": 2,
                        "text": (
                            "This piece nails it. We're not building artificial humans, "
                            "we're building artificial usefulness. https://example.com"
                        ),
                        "style_label": "provocative",
                    },
                    {
                        "id": 3,
                        "text": (
                            "Reading this made me rethink how we frame AI. Stop asking "
                            "'can it think?' Start asking 'can it help?' https://example.com"
                        ),
                        "style_label": "reflective",
                    },
                ],
            }
        }
    )
