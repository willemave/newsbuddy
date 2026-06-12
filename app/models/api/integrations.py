# ruff: noqa: F401
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.constants import TWEET_SUGGESTION_MODEL
from app.models.api.base import UTCDateTime
from app.models.api.pagination import PaginationMetadata
from app.models.contracts import (
    ContentClassification,
    ContentStatus,
    ContentType,
    DeleteStatus,
    IntegrationDisconnectStatus,
    SummaryKind,
    SummaryVersion,
    UserLlmProvider,
)


class XOAuthStartRequest(BaseModel):
    """Request to begin X OAuth flow."""

    twitter_username: str | None = Field(default=None, max_length=50)


class XOAuthStartResponse(BaseModel):
    """Response payload for X OAuth start."""

    authorize_url: str
    state: str
    scopes: list[str] = Field(default_factory=list)


class XOAuthExchangeRequest(BaseModel):
    """Request to exchange an X OAuth authorization code."""

    code: str = Field(..., min_length=1, max_length=4096)
    state: str = Field(..., min_length=1, max_length=255)


class XConnectionResponse(BaseModel):
    """Current X integration connection state for a user."""

    provider: str
    connected: bool
    is_active: bool
    provider_user_id: str | None = None
    provider_username: str | None = None
    scopes: list[str] = Field(default_factory=list)
    last_synced_at: UTCDateTime | None = None
    last_status: str | None = None
    last_error: str | None = None
    twitter_username: str | None = None


class IntegrationDisconnectResponse(BaseModel):
    """Response for integration disconnect actions."""

    status: IntegrationDisconnectStatus = IntegrationDisconnectStatus.DISCONNECTED
    provider: str = "x"


class UserLlmIntegrationResponse(BaseModel):
    """User-managed LLM integration summary."""

    provider: UserLlmProvider
    configured: bool
    updated_at: UTCDateTime | None = None


class UpsertUserLlmIntegrationRequest(BaseModel):
    """Request to store a user-managed LLM provider key."""

    api_key: str = Field(..., min_length=1, max_length=4096)


class UserLlmIntegrationTestResponse(BaseModel):
    """Response for validating presence of a user-managed LLM key."""

    provider: UserLlmProvider
    ok: bool


class DeleteUserLlmIntegrationResponse(BaseModel):
    """Response for deleting a user-managed LLM provider key."""

    status: DeleteStatus = DeleteStatus.DELETED
    provider: str
