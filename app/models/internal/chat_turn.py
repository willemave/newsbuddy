"""Immutable inputs for one queued chat turn."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.internal.assistant import AssistantScreenContext


class ChatTurnSessionSnapshot(BaseModel):
    """Session fields whose acceptance-time values define a queued turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: int = Field(gt=0)
    effective_session_id: int = Field(gt=0)
    visible_session_id: int = Field(gt=0)
    model: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=50)
    title: str | None = Field(default=None, max_length=500)
    session_type: str | None = Field(default=None, max_length=50)
    content_id: int | None = Field(default=None, gt=0)
    news_item_id: int | None = Field(default=None, gt=0)
    parent_session_id: int | None = Field(default=None, gt=0)
    topic: str | None = Field(default=None, max_length=500)
    context_snapshot: str | None = None
    is_hidden_from_history: bool = False
    council_persona_id: str | None = Field(default=None, max_length=64)
    council_persona_name: str | None = Field(default=None, max_length=120)
    council_persona_prompt: str | None = None


class ChatTurnProcessingContext(BaseModel):
    """Versioned routing and prompt snapshot stored on a processing message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    kind: Literal["article", "assistant", "council", "deep_research"]
    user_prompt: str = Field(min_length=1, max_length=10_000)
    source: str = Field(min_length=1, max_length=50)
    session: ChatTurnSessionSnapshot
    screen_context: AssistantScreenContext | None = None

    @model_validator(mode="after")
    def validate_routing_context(self) -> ChatTurnProcessingContext:
        """Require screen context only for assistant-routed turns."""
        if self.kind == "assistant" and self.screen_context is None:
            raise ValueError("Assistant chat turns require screen_context")
        if self.kind != "assistant" and self.screen_context is not None:
            raise ValueError("Only assistant chat turns accept screen_context")
        return self
