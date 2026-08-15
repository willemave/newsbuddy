from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    field_validator,
)

from app.models.api.base import UTCDateTime
from app.models.api.llm_tasks import LlmTaskActionResponse
from app.models.contracts import LlmTaskApprovalPolicy, LlmTaskMode, LlmTaskStatus

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)

SHARE_ACTION_MODES = {
    LlmTaskMode.ADD_CONTENT,
    LlmTaskMode.ADD_TO_BRIEFING,
    LlmTaskMode.ADD_LINKS,
    LlmTaskMode.ADD_FEED,
    LlmTaskMode.CHAT,
    LlmTaskMode.PRESENTATION,
    LlmTaskMode.BOOKMARK_ONLY,
}


class ShareActionCreateRequest(BaseModel):
    """Request to run a VM-backed ShareSheet action workflow."""

    url: str = Field(..., description="Submitted http/https URL")
    mode: LlmTaskMode = Field(..., description="ShareSheet action mode to run")
    instruction: str | None = Field(
        None,
        max_length=4000,
        validation_alias=AliasChoices("instruction", "note"),
        description="Optional user instruction for the share action",
    )
    chat_initial_message: str | None = Field(
        None,
        max_length=2000,
        description="Initial user question for chat mode",
    )
    interests_prompt: str | None = Field(
        None,
        max_length=4000,
        description="Optional user instructions for Learning Deck generation",
    )
    approval_policy: dict[str, LlmTaskApprovalPolicy] | None = Field(
        None,
        description="Optional workflow approval policy overrides",
    )

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, value: object) -> str:
        """Validate URL while keeping a plain string API contract."""
        return str(_HTTP_URL_ADAPTER.validate_python(value))

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: LlmTaskMode) -> LlmTaskMode:
        """Restrict this endpoint to ShareSheet modes."""
        if value not in SHARE_ACTION_MODES:
            raise ValueError(f"Unsupported share action mode: {value.value}")
        return value

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "url": "https://example.com/article",
                "mode": "add_feed",
                "instruction": "Find the publication feed",
                "approval_policy": {"default": "auto_apply"},
            }
        }
    )


class ShareActionResponse(BaseModel):
    """Response describing a queued or running Share Action LLM task."""

    task_id: int
    mode: LlmTaskMode
    status: LlmTaskStatus
    workflow_state: str
    created_at: UTCDateTime
    actions: list[LlmTaskActionResponse] = Field(default_factory=list)


class ShareActionCandidate(BaseModel):
    """One content URL candidate found by a Share Action agent."""

    url: str
    title: str | None = None
    platform: str | None = None
    content_type: str | None = None
    rationale: str | None = None


class ShareActionPresentationCandidate(BaseModel):
    """Presentation generation intent or direct artifact metadata."""

    source_url: str | None = None
    title: str | None = None
    interests_prompt: str | None = None
    artifact_mode: str | None = None


class ShareActionChatCandidate(BaseModel):
    """Chat handoff intent from a Share Action agent."""

    content_url: str | None = None
    initial_message: str | None = None


class ShareActionBriefingTargetBase(BaseModel):
    """Shared fields for one resolved Add-to-Briefing target."""

    url: str
    title: str | None = None
    platform: str | None = None
    rationale: str | None = None

    model_config = ConfigDict(extra="forbid")


class ShareActionBriefingFeedTarget(ShareActionBriefingTargetBase):
    """Continuing source resolved to a canonical feed URL."""

    kind: Literal["feed"]


class ShareActionBriefingContentTarget(ShareActionBriefingTargetBase):
    """Individual item resolved to a canonical Briefing-eligible URL."""

    kind: Literal["content"]
    content_type: str | None = None


type ShareActionBriefingTarget = Annotated[
    ShareActionBriefingFeedTarget | ShareActionBriefingContentTarget,
    Field(discriminator="kind"),
]


class ShareActionAgentResult(BaseModel):
    """Typed final result read from `output/result.json`."""

    action: str = Field(..., description="Final action family or no_action")
    primary_url: str | None = None
    feed_url: str | None = None
    content_urls: list[ShareActionCandidate] = Field(default_factory=list)
    presentation: ShareActionPresentationCandidate | None = None
    chat: ShareActionChatCandidate | None = None
    briefing_target: ShareActionBriefingTarget | None = None
    title: str | None = None
    platform: str | None = None
    content_type: str | None = None
    rationale: str | None = None
    sources_used: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
