"""Chat DTOs for API responses."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.api.base import UTCDateTime, lenient_field
from app.models.api.pagination import PaginationMetadata
from app.models.contracts import (
    ChatMessageDisplayType,
    ChatMessageRole,
    LLMProvider,
    MessageProcessingStatus,
)
from app.models.domain.chat_render import AssistantFeedOption, CouncilCandidate
from app.models.internal.assistant import AssistantScreenContext


class CreateChatSessionRequest(BaseModel):
    """Request to create a new chat session."""

    content_id: int | None = Field(None, description="Content ID to chat about")
    news_item_id: int | None = Field(None, description="News item ID to chat about")
    topic: str | None = Field(None, max_length=500, description="Specific topic to discuss")
    llm_provider: LLMProvider | None = Field(None, description="LLM provider (defaults to openai)")
    llm_model_hint: str | None = Field(
        None, max_length=100, description="Optional specific model to use"
    )
    initial_message: str | None = Field(
        None, max_length=2000, description="Optional initial user message"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content_id": 123,
                "news_item_id": None,
                "topic": None,
                "llm_provider": "openai",
                "llm_model_hint": None,
                "initial_message": "What are the key insights from this article?",
            }
        }
    )


class UpdateChatSessionRequest(BaseModel):
    """Request to update a chat session."""

    llm_provider: LLMProvider | None = Field(
        None, description="New LLM provider to use for this session"
    )
    llm_model_hint: str | None = Field(
        None, max_length=100, description="Optional specific model to use"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "llm_provider": "openai",
                "llm_model_hint": None,
            }
        }
    )


class SendChatMessageRequest(BaseModel):
    """Request to send a message in a chat session."""

    message: str = Field(..., min_length=1, max_length=10000, description="Message to send")

    model_config = ConfigDict(
        json_schema_extra={"example": {"message": "Can you explain that in more detail?"}}
    )


class CouncilStartRequest(BaseModel):
    """Request to start council mode from an existing parent session."""

    message: str = Field(..., min_length=1, max_length=10000)


class CouncilSelectRequest(BaseModel):
    """Request to switch the active branch for a council chat."""

    child_session_id: int = Field(..., ge=1)


class CouncilRetryRequest(BaseModel):
    """Request to retry one failed council branch."""

    child_session_id: int = Field(..., ge=1)


class AssistantScreenContextDto(AssistantScreenContext):
    """API schema wrapper for assistant screen context."""


class AssistantTurnRequest(BaseModel):
    """Request to create or continue a screen-aware assistant conversation."""

    message: str = Field(..., min_length=1, max_length=10000)
    session_id: int | None = Field(default=None, ge=1)
    screen_context: AssistantScreenContextDto = Field(default_factory=AssistantScreenContextDto)


class ChatMessageDto(BaseModel):
    """Flattened chat message returned to clients."""

    id: int = Field(..., description="Unique message identifier")
    source_message_id: int | None = Field(
        default=None,
        description="Backing async chat_messages row ID used for status polling",
    )
    display_key: str = Field(
        default="",
        description=(
            "Stable transcript row identity shared by session-detail and status endpoints"
        ),
    )
    session_id: int = Field(..., description="Chat session ID")
    role: ChatMessageRole = Field(..., description="Message role")
    content: str = Field(..., description="Message content")
    timestamp: UTCDateTime = Field(..., description="Timestamp when message was stored")
    display_type: ChatMessageDisplayType = Field(
        default=ChatMessageDisplayType.MESSAGE,
        description="Display treatment for this row in the chat transcript",
    )
    process_label: str | None = Field(
        default=None,
        description="Compact label for process-summary rows",
    )
    status: MessageProcessingStatus = Field(
        default=MessageProcessingStatus.COMPLETED,
        description="Processing status for async messages",
    )
    error: str | None = Field(default=None, description="Error message if processing failed")
    feed_options: list[AssistantFeedOption] = lenient_field(
        default_factory=list,
        description="Optional validated feed options attached to the assistant message",
    )
    council_candidates: list[CouncilCandidate] = lenient_field(
        default_factory=list,
        description="Optional council reply candidates attached to the assistant message",
    )
    active_council_child_session_id: int | None = Field(
        default=None,
        description="Currently selected council branch for council candidate rows",
    )

    @model_validator(mode="after")
    def populate_display_key(self) -> Self:
        """Derive a stable row key when callers do not provide one explicitly."""
        if not self.display_key:
            source_id = self.source_message_id if self.source_message_id is not None else self.id
            self.display_key = f"server|{source_id}|{self.role.value}|{self.display_type.value}"
        return self


class ChatSessionSummaryDto(BaseModel):
    """Summary of a chat session."""

    id: int
    title: str | None
    content_id: int | None
    news_item_id: int | None
    session_type: str | None
    topic: str | None
    llm_model: str
    llm_provider: str
    created_at: UTCDateTime
    updated_at: UTCDateTime | None
    last_message_at: UTCDateTime | None
    is_archived: bool
    article_title: str | None = None
    article_url: str | None = None
    article_summary: str | None = Field(
        default=None,
        description="Short summary of the article (for empty session display)",
    )
    article_source: str | None = Field(
        default=None,
        description="Source name of the article (for empty session display)",
    )
    article_image_url: str | None = Field(
        default=None,
        description="Resolved full-size image URL for the linked content",
    )
    article_thumbnail_url: str | None = Field(
        default=None,
        description="Resolved thumbnail URL for the linked content",
    )
    has_pending_message: bool = Field(
        default=False,
        description="True if session has a message currently being processed",
    )
    is_saved_to_knowledge: bool = Field(
        default=False,
        description="True if the linked content is saved to the user's knowledge library",
    )
    has_messages: bool = Field(
        default=True,
        description="True if session has any messages (false for new saved items)",
    )
    last_message_preview: str | None = Field(
        default=None,
        description="Truncated preview of the most recent message in the session",
    )
    last_message_role: str | None = Field(
        default=None,
        description="Role of the last message (user or assistant)",
    )
    council_mode: bool = Field(
        default=False,
        description="True when this visible session is using council chat mode",
    )
    active_child_session_id: int | None = Field(
        default=None,
        description="Currently selected hidden branch session for council chat",
    )


class ChatSessionDetailDto(BaseModel):
    """Chat session with message history."""

    session: ChatSessionSummaryDto
    messages: list[ChatMessageDto]


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionSummaryDto]
    meta: PaginationMetadata


class SendMessageResponse(BaseModel):
    """Response after sending a chat message (async).

    Returns immediately with the user message and a message_id to poll for completion.
    """

    session_id: int
    user_message: ChatMessageDto = Field(..., description="The user's message")
    message_id: int = Field(..., description="ID to poll for assistant response")
    status: MessageProcessingStatus = Field(
        default=MessageProcessingStatus.PROCESSING,
        description="Current processing status",
    )


class AssistantTurnResponse(BaseModel):
    """Response returned after creating or continuing an assistant turn."""

    session: ChatSessionSummaryDto
    user_message: ChatMessageDto
    message_id: int = Field(..., description="Pending assistant message identifier")
    status: MessageProcessingStatus = Field(
        default=MessageProcessingStatus.PROCESSING,
        description="Current processing status",
    )


class MessageStatusResponse(BaseModel):
    """Response when polling for message completion status."""

    message_id: int
    status: MessageProcessingStatus
    assistant_message: ChatMessageDto | None = Field(
        default=None,
        description="Assistant response (present when status=completed)",
    )
    error: str | None = Field(default=None, description="Error message if status=failed")


class CreateChatSessionResponse(BaseModel):
    """Response wrapper for session creation."""

    session: ChatSessionSummaryDto
