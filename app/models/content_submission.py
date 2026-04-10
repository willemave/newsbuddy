"""Pydantic models for content submission workflows."""

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl

from app.models.metadata import ContentStatus, ContentType


class SubmitContentRequest(BaseModel):
    """Request to submit a user-provided URL for processing."""

    url: HttpUrl = Field(..., description="URL to submit (http/https only)")
    content_type: ContentType | None = Field(
        None,
        description="Content type hint. If omitted, the server will infer based on the URL.",
    )
    title: str | None = Field(
        None,
        max_length=500,
        description="Optional title supplied by the client/share sheet",
    )
    platform: str | None = Field(
        None, max_length=50, description="Optional platform hint (e.g., spotify, substack)"
    )
    instruction: str | None = Field(
        None,
        max_length=4000,
        validation_alias=AliasChoices("instruction", "note"),
        description="Optional instruction for analyzing the submitted URL",
    )
    crawl_links: bool = Field(
        False,
        description=(
            "Whether to create additional content items from relevant links "
            "discovered on the submitted page."
        ),
    )
    subscribe_to_feed: bool = Field(
        False,
        description=(
            "When true, detect an RSS/Atom feed from the URL and subscribe to it "
            "instead of processing the URL as content."
        ),
    )
    share_and_chat: bool = Field(
        False,
        description=(
            "When true, mark the submitted content as read and start a dig-deeper chat "
            "after processing completes."
        ),
    )
    save_to_knowledge_and_mark_read: bool = Field(
        False,
        description=(
            "When true, download and summarize the submitted content, then mark it as "
            "read and save it to the user's knowledge library."
        ),
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "url": "https://open.spotify.com/episode/abc123",
                "content_type": "podcast",
                "title": "Great interview about AI",
                "platform": "spotify",
                "instruction": "Add all links mentioned in the episode page",
                "crawl_links": True,
                "subscribe_to_feed": False,
                "share_and_chat": False,
                "save_to_knowledge_and_mark_read": False,
            }
        }
    )


class ContentSubmissionResponse(BaseModel):
    """Response describing the result of a user submission."""

    content_id: int = Field(..., description="ID of the created or existing content")
    content_type: ContentType = Field(..., description="Content type that will be processed")
    status: ContentStatus = Field(..., description="Current processing status of the content")
    platform: str | None = Field(None, description="Normalized platform name if available")
    already_exists: bool = Field(
        False, description="Whether the submission matched an existing record"
    )
    message: str = Field(..., description="Human-readable status message")
    task_id: int | None = Field(None, description="Processing task ID enqueued for this content")
    source: str | None = Field(
        None, description="Source attribution recorded for the content (self submission)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content_id": 42,
                "content_type": "podcast",
                "status": "new",
                "platform": "spotify",
                "already_exists": False,
                "message": "Content queued for processing",
                "task_id": 101,
                "source": "self submission",
            }
        }
    )
