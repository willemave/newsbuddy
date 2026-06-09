# ruff: noqa: F401
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.constants import TWEET_SUGGESTION_MODEL
from app.models.api.content_discussions import ContentDiscussionResponse
from app.models.api.pagination import PaginationMetadata
from app.models.contracts import (
    ContentClassification,
    ContentStatus,
    ContentType,
    SummaryKind,
    SummaryVersion,
)


class ContentSummaryResponse(BaseModel):
    """Summary information for a content item in list view."""

    id: int = Field(..., description="Unique identifier")
    content_type: ContentType = Field(..., description="Type of content (article/podcast/news)")
    url: str = Field(..., description="Canonical URL of the content")
    source_url: str | None = Field(None, description="Original scraped/submitted URL")
    discussion_url: str | None = Field(
        None, description="Discussion URL (tweet, HN thread, etc.) when available"
    )
    title: str | None = Field(None, description="Content title")
    source: str | None = Field(
        None, description="Content source (e.g., substack name, podcast name)"
    )
    platform: str | None = Field(
        None, description="Content platform (e.g., twitter, substack, youtube)"
    )
    status: ContentStatus = Field(..., description="Processing status")
    short_summary: str | None = Field(
        None,
        description=(
            "Short summary for display; for news items this returns the excerpt or first item text"
        ),
    )
    created_at: str = Field(..., description="ISO timestamp when content was created")
    processed_at: str | None = Field(None, description="ISO timestamp when content was processed")
    classification: ContentClassification | None = Field(
        None, description="Content classification (to_read/skip)"
    )
    publication_date: str | None = Field(
        None, description="ISO timestamp of when content was published"
    )
    is_read: bool = Field(False, description="Whether the content has been marked as read")
    is_saved_to_knowledge: bool = Field(
        False,
        description="Whether the content has been saved to the user's knowledge library",
    )
    news_article_url: str | None = Field(
        None, description="Canonical article link for news content"
    )
    news_discussion_url: str | None = Field(
        None, description="Aggregator discussion URL (HN thread, tweet, etc.)"
    )
    news_key_points: list[str] | None = Field(
        None, description="Key points provided for news items"
    )
    news_summary: str | None = Field(None, description="Short overview synthesized for news items")
    user_status: str | None = Field(
        None, description="Per-user content status (e.g., inbox, archived)"
    )
    image_url: str | None = Field(
        None, description="URL of full-size AI-generated image for this content"
    )
    thumbnail_url: str | None = Field(
        None, description="URL of 200px thumbnail image for fast loading in list views"
    )
    primary_topic: str | None = Field(
        None, description="Primary topic extracted from summary topics or platform name"
    )
    top_comment: dict[str, str] | None = Field(
        None, description="First discussion comment {author, text} for preview"
    )
    comment_count: int | None = Field(
        None, description="Discussion comment count from aggregator or discussion fetcher"
    )
    feed_preview: dict[str, Any] | None = Field(
        None, description="Longform artifact feed preview payload"
    )
    artifact_type: str | None = Field(None, description="Longform artifact type")
    preview_bullets: list[str] | None = Field(
        None, description="Longform artifact feed-preview bullets"
    )
    reason_to_read: str | None = Field(
        None, description="Feed-preview reason explaining why the item is worth opening"
    )
    key_takeaway: str | None = Field(
        None, description="Key takeaway to display under long-form list titles"
    )
    saved_source: Literal["knowledge", "x_bookmark"] | None = Field(
        None,
        description="Saved-library source for this content when it appears in saved views",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 123,
                "content_type": "article",
                "url": "https://example.com/article",
                "title": "Understanding AI in 2025",
                "source": "Tech Blog",
                "platform": "substack",
                "status": "completed",
                "short_summary": "This article explores the latest developments in AI...",
                "created_at": "2025-06-19T10:30:00Z",
                "processed_at": "2025-06-19T10:35:00Z",
                "classification": "to_read",
                "publication_date": "2025-06-18T12:00:00Z",
                "is_read": False,
                "image_url": "/static/images/content/123.png",
                "thumbnail_url": "/static/images/thumbnails/123.png",
                "primary_topic": "AI",
                "top_comment": {"author": "user123", "text": "Great article!"},
                "saved_source": "knowledge",
            }
        }
    )


class ContentListResponse(BaseModel):
    """Response for content list endpoint."""

    contents: list[ContentSummaryResponse] = Field(..., description="List of content items")
    available_dates: list[str] = Field(..., description="List of available dates (YYYY-MM-DD)")
    content_types: list[ContentType] = Field(
        ..., description="Available content types for filtering"
    )
    meta: PaginationMetadata = Field(..., description="Pagination metadata for the response")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "contents": [
                    {
                        "id": 123,
                        "content_type": "article",
                        "url": "https://example.com/article",
                        "title": "Understanding AI in 2025",
                        "source": "Tech Blog",
                        "platform": "substack",
                        "status": "completed",
                        "short_summary": "This article explores...",
                        "created_at": "2025-06-19T10:30:00Z",
                        "processed_at": "2025-06-19T10:35:00Z",
                        "classification": "to_read",
                    }
                ],
                "available_dates": ["2025-06-19", "2025-06-18"],
                "content_types": ["article", "podcast", "news"],
                "meta": {
                    "next_cursor": "eyJsYXN0X2lkIjoxMjN9",
                    "has_more": True,
                    "page_size": 25,
                    "total": 1,
                },
            }
        }
    )


class NarrationResponse(BaseModel):
    """Unified narration payload for any supported narration target."""

    target_type: Literal["content"] = Field(
        ...,
        description="Narration target family",
    )
    target_id: int = Field(..., description="Target identifier within the target family")
    title: str = Field(..., description="Resolved title for spoken playback")
    narration_text: str = Field(..., description="Plain-text narration script for voice playback")


class DetectedFeed(BaseModel):
    """Detected RSS/Atom feed from content page."""

    url: str = Field(..., description="Feed URL")
    type: str = Field(..., description="Feed type: substack, podcast_rss, or atom")
    title: str | None = Field(None, description="Feed title from link tag")
    format: str = Field("rss", description="Feed format: rss or atom")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "url": "https://example.substack.com/feed",
                "type": "substack",
                "title": "Example Newsletter",
                "format": "rss",
            }
        }
    )


class SubmissionFeedInitialDownloadResponse(BaseModel):
    """Initial download result for a newly subscribed feed."""

    requested_count: int | None = Field(
        None, description="Number of recent feed items requested for initial download"
    )
    ran: bool | None = Field(None, description="Whether initial download was attempted")
    status: str | None = Field(None, description="Initial download status")
    reason: str | None = Field(None, description="Reason initial download was skipped")
    error: str | None = Field(None, description="Initial download failure reason")
    config_id: int | None = Field(None, description="Feed config used for the download")
    base_limit: int | None = Field(None, description="Original scraper limit")
    target_limit: int | None = Field(None, description="Temporary scraper limit used")
    scraped: int | None = Field(None, description="Number of items scraped")
    saved: int | None = Field(None, description="Number of items saved")
    duplicates: int | None = Field(None, description="Number of duplicate items ignored")
    errors: int | None = Field(None, description="Number of scraper item errors")


class SubmissionFeedSubscriptionResponse(BaseModel):
    """Feed subscription outcome attached to a submission status row."""

    status: str = Field(..., description="Raw feed subscription status")
    feed_url: str | None = Field(None, description="Subscribed feed URL")
    feed_type: str | None = Field(None, description="Subscribed feed type")
    created: bool | None = Field(None, description="Whether a new feed config was created")
    config_id: int | None = Field(None, description="Created feed config identifier")
    initial_download: SubmissionFeedInitialDownloadResponse | None = Field(
        None, description="Initial recent-item download result"
    )


SubmissionKind = Literal["content", "feed_subscription"]
SubmissionOutcome = Literal[
    "queued",
    "processing",
    "completed",
    "failed",
    "skipped",
    "subscribed",
    "already_subscribed",
    "feed_not_found",
    "feed_fetch_failed",
    "feed_subscription_failed",
]


class SubmissionStatusResponse(BaseModel):
    """Status information for a user-submitted content item."""

    id: int = Field(..., description="Unique identifier")
    content_type: ContentType = Field(
        ..., description="Type of content (article/podcast/news/unknown)"
    )
    url: str = Field(..., description="Canonical URL of the content")
    source_url: str | None = Field(None, description="Original submitted URL")
    title: str | None = Field(None, description="Content title (if detected)")
    status: ContentStatus = Field(..., description="Processing status")
    error_message: str | None = Field(None, description="Failure reason when status=failed/skipped")
    created_at: str = Field(..., description="ISO timestamp when content was created")
    processed_at: str | None = Field(None, description="ISO timestamp when content was processed")
    submitted_via: str | None = Field(None, description="Submission channel (share_sheet, etc.)")
    is_self_submission: bool = Field(
        True, description="Whether this content was submitted by the current user"
    )
    submission_kind: SubmissionKind = Field(
        "content", description="Semantic submission kind for user-facing display"
    )
    outcome: SubmissionOutcome = Field(
        "processing", description="Semantic submission outcome for user-facing display"
    )
    detected_feed: DetectedFeed | None = Field(
        None, description="RSS/Atom feed detected while handling a feed subscription request"
    )
    feed_subscription: SubmissionFeedSubscriptionResponse | None = Field(
        None, description="Feed subscription result for Add Feed submissions"
    )


class SubmissionStatusListResponse(BaseModel):
    """Response for user submission status list."""

    submissions: list[SubmissionStatusResponse] = Field(
        ..., description="List of user-submitted items still processing or failed"
    )
    meta: PaginationMetadata = Field(..., description="Pagination metadata for the response")


class PodcastEpisodeSearchResultResponse(BaseModel):
    """Single podcast episode search result."""

    title: str
    episode_url: str
    podcast_title: str | None = None
    source: str | None = None
    snippet: str | None = None
    feed_url: str | None = None
    published_at: str | None = None
    provider: str | None = None
    score: float | None = None


class PodcastEpisodeSearchResponse(BaseModel):
    """Response payload for podcast episode search."""

    results: list[PodcastEpisodeSearchResultResponse] = Field(default_factory=list)


class MixedSearchFeedResultResponse(BaseModel):
    """Validated feed/source result for mixed search."""

    id: str
    title: str
    site_url: str
    feed_url: str
    feed_type: str
    feed_format: str
    description: str | None = None
    rationale: str | None = None
    evidence_url: str | None = None


class MixedSearchResponse(BaseModel):
    """Sectioned mixed search results for the More > Search screen."""

    query: str
    content: list[ContentSummaryResponse] = Field(default_factory=list)
    feeds: list[MixedSearchFeedResultResponse] = Field(default_factory=list)
    podcasts: list[PodcastEpisodeSearchResultResponse] = Field(default_factory=list)


class ContentDetailResponse(BaseModel):
    """Detailed response for a single content item."""

    id: int = Field(..., description="Unique identifier")
    content_type: ContentType = Field(..., description="Type of content (article/podcast/news)")
    url: str = Field(..., description="Canonical URL of the content")
    source_url: str | None = Field(None, description="Original scraped/submitted URL")
    discussion_url: str | None = Field(
        None, description="Discussion URL (tweet, HN thread, etc.) when available"
    )
    title: str | None = Field(None, description="Content title")
    display_title: str = Field(
        ..., description="Display title (prefers summary title over content title)"
    )
    source: str | None = Field(None, description="Content source")
    status: ContentStatus = Field(..., description="Processing status")
    error_message: str | None = Field(None, description="Error message if processing failed")
    retry_count: int = Field(..., description="Number of retry attempts")
    metadata: dict[str, Any] = Field(..., description="Content-specific metadata")
    created_at: str = Field(..., description="ISO timestamp when content was created")
    updated_at: str | None = Field(None, description="ISO timestamp of last update")
    processed_at: str | None = Field(None, description="ISO timestamp when content was processed")
    checked_out_by: str | None = Field(None, description="Worker ID that checked out this content")
    checked_out_at: str | None = Field(
        None, description="ISO timestamp when content was checked out"
    )
    publication_date: str | None = Field(
        None, description="ISO timestamp of when content was published"
    )
    is_read: bool = Field(False, description="Whether the content has been marked as read")
    is_saved_to_knowledge: bool = Field(
        False,
        description="Whether the content has been saved to the user's knowledge library",
    )
    # Additional useful properties from ContentData
    summary: str | None = Field(None, description="Summary text")
    short_summary: str | None = Field(None, description="Short version of summary for list view")
    summary_kind: SummaryKind | None = Field(
        None, description="Summary kind discriminator (e.g., long_interleaved)"
    )
    summary_version: SummaryVersion | None = Field(
        None, description="Summary schema version for the current summary kind"
    )
    structured_summary: dict[str, Any] | None = Field(
        None, description="Structured summary with bullet points and quotes"
    )
    longform_artifact: dict[str, Any] | None = Field(
        None, description="Typed longform artifact envelope"
    )
    feed_preview: dict[str, Any] | None = Field(
        None, description="Longform artifact feed preview payload"
    )
    artifact_type: str | None = Field(None, description="Longform artifact type")
    preview_bullets: list[str] | None = Field(
        None, description="Longform artifact feed-preview bullets"
    )
    reason_to_read: str | None = Field(
        None, description="Feed-preview reason explaining why the item is worth opening"
    )
    bullet_points: list[dict[str, str]] = Field(
        ..., description="Bullet points from structured summary"
    )
    quotes: list[dict[str, str]] = Field(..., description="Quotes from structured summary")
    topics: list[str] = Field(..., description="Topics from structured summary")
    full_markdown: str | None = Field(
        None, description="Full article content formatted as markdown"
    )
    body_available: bool = Field(False, description="Whether canonical body text is available")
    body_kind: str | None = Field(
        None, description="Resolved body kind (article, transcript, or source)"
    )
    body_format: str | None = Field(None, description="Resolved body format (text or markdown)")
    news_article_url: str | None = Field(
        None, description="Canonical article link for news content"
    )
    news_discussion_url: str | None = Field(
        None, description="Aggregator discussion URL (HN thread, tweet, etc.)"
    )
    news_key_points: list[str] | None = Field(
        None, description="Key points provided for news items"
    )
    news_summary: str | None = Field(None, description="Short overview synthesized for news items")
    image_url: str | None = Field(
        None, description="URL of full-size AI-generated image for this content"
    )
    thumbnail_url: str | None = Field(
        None, description="URL of 200px thumbnail image for fast loading"
    )
    detected_feed: DetectedFeed | None = Field(
        None, description="Detected RSS/Atom feed for this content"
    )
    can_subscribe: bool = Field(
        False,
        description="Whether the current user can subscribe to the detected feed",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 123,
                "content_type": "article",
                "url": "https://example.com/article",
                "title": "Understanding AI in 2025",
                "source": "Tech Blog",
                "status": "completed",
                "error_message": None,
                "retry_count": 0,
                "metadata": {
                    "source": "Tech Blog",
                    "author": "Jane Doe",
                    "publication_date": "2025-06-19T00:00:00Z",
                    "content_type": "html",
                    "word_count": 1500,
                    "summary": {
                        "title": "Understanding AI in 2025",
                        "overview": "This article explores the latest developments...",
                        "bullet_points": [
                            {"text": "AI is transforming industries", "category": "key_finding"}
                        ],
                        "quotes": [{"text": "The future is now", "context": "Jane Doe"}],
                        "topics": ["AI", "Technology", "Future"],
                        "summarization_date": "2025-06-19T10:35:00Z",
                        "classification": "to_read",
                    },
                },
                "created_at": "2025-06-19T10:30:00Z",
                "updated_at": "2025-06-19T10:35:00Z",
                "processed_at": "2025-06-19T10:35:00Z",
                "checked_out_by": None,
                "checked_out_at": None,
                "publication_date": "2025-06-18T12:00:00Z",
                "is_read": False,
                "display_title": "Understanding AI in 2025",
                "summary": "This article explores the latest developments...",
                "short_summary": "This article explores the latest developments...",
                "structured_summary": {
                    "title": "Understanding AI in 2025",
                    "overview": "This article explores the latest developments...",
                    "bullet_points": [
                        {"text": "AI is transforming industries", "category": "key_finding"}
                    ],
                    "quotes": [{"text": "The future is now", "context": "Jane Doe"}],
                    "topics": ["AI", "Technology", "Future"],
                    "summarization_date": "2025-06-19T10:35:00Z",
                    "classification": "to_read",
                },
                "bullet_points": [
                    {"text": "AI is transforming industries", "category": "key_finding"}
                ],
                "quotes": [{"text": "The future is now", "context": "Jane Doe"}],
                "topics": ["AI", "Technology", "Future"],
                "full_markdown": "# Understanding AI in 2025\n\nFull article content...",
                "body_available": True,
                "body_kind": "article",
                "body_format": "text",
                "image_url": "/static/images/content/123.png",
                "thumbnail_url": "/static/images/thumbnails/123.png",
                "can_subscribe": False,
            }
        }
    )


class ContentBodyResponse(BaseModel):
    """Resolved canonical body payload."""

    content_id: int = Field(..., description="Content identifier")
    variant: str = Field(..., description="Canonical body variant")
    kind: str = Field(..., description="Body kind")
    format: str = Field(..., description="Body format")
    text: str = Field(..., description="Full canonical body text")
    updated_at: str | None = Field(None, description="ISO timestamp of the body pointer update")
