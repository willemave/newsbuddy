from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.api.base import UTCDateTime
from app.models.contracts import LlmTaskActionStatus, LlmTaskApprovalPolicy


class LlmTaskActionResponse(BaseModel):
    """Client-facing view of one host-mediated LLM task action."""

    id: int
    llm_task_id: int
    action_name: str
    action_status: LlmTaskActionStatus
    approval_policy: LlmTaskApprovalPolicy
    approval_required: bool
    action_input: dict[str, Any] = Field(default_factory=dict)
    action_result: dict[str, Any] = Field(default_factory=dict)
    rationale: str | None = None
    idempotency_key: str | None = None
    approved_by_user_id: int | None = None
    error_message: str | None = None
    created_at: UTCDateTime
    approved_at: UTCDateTime | None = None
    started_at: UTCDateTime | None = None
    completed_at: UTCDateTime | None = None


class LlmTaskActionListResponse(BaseModel):
    """List response for actions attached to one LLM task."""

    actions: list[LlmTaskActionResponse] = Field(default_factory=list)


class LlmTaskActionRejectRequest(BaseModel):
    """Request body for rejecting a proposed or approval-pending action."""

    reason: str | None = Field(default=None, max_length=1000)


class LlmTaskWebSearchRequest(BaseModel):
    """Internal VM tool request for host-mediated web search."""

    query: str = Field(..., min_length=1, max_length=1000)
    num_results: int = Field(default=5, ge=1, le=10)
    category: str | None = Field(default=None, max_length=100)


class LlmTaskWebSearchResult(BaseModel):
    """One host-mediated search result returned to a VM task."""

    title: str
    url: str
    snippet: str | None = None
    published_date: str | None = None


class LlmTaskWebSearchResponse(BaseModel):
    """Internal VM tool response for host-mediated web search."""

    query: str
    results: list[LlmTaskWebSearchResult] = Field(default_factory=list)
