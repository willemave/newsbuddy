# ruff: noqa: F401
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.constants import TWEET_SUGGESTION_MODEL
from app.models.api.pagination import PaginationMetadata
from app.models.contracts import (
    ContentClassification,
    ContentStatus,
    ContentType,
    SummaryKind,
    SummaryVersion,
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
    last_synced_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    twitter_username: str | None = None


class IntegrationDisconnectResponse(BaseModel):
    """Response for integration disconnect actions."""

    status: Literal["disconnected"] = "disconnected"
    provider: str = "x"


class UserLlmIntegrationResponse(BaseModel):
    """User-managed LLM integration summary."""

    provider: Literal["anthropic", "openai", "google"]
    configured: bool
    updated_at: datetime | None = None


class UpsertUserLlmIntegrationRequest(BaseModel):
    """Request to store a user-managed LLM provider key."""

    api_key: str = Field(..., min_length=1, max_length=4096)


class UserLlmIntegrationTestResponse(BaseModel):
    """Response for validating presence of a user-managed LLM key."""

    provider: Literal["anthropic", "openai", "google"]
    ok: bool
