# ruff: noqa: F401
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.constants import TWEET_SUGGESTION_MODEL
from app.models.api.base import UTCDateTime
from app.models.api.pagination import PaginationMetadata
from app.models.contracts import (
    CliLinkStatus,
    ContentClassification,
    ContentStatus,
    ContentType,
    SummaryKind,
    SummaryVersion,
)


class CliLinkStartRequest(BaseModel):
    """Request to start a new CLI QR-link session."""

    device_name: str | None = Field(default=None, max_length=255)


class CliLinkStartResponse(BaseModel):
    """Unauthenticated response for bootstrapping CLI QR login."""

    session_id: str
    status: CliLinkStatus
    poll_token: str
    approve_url: str
    expires_at: UTCDateTime
    poll_interval_seconds: int = 2


class CliLinkApproveRequest(BaseModel):
    """Authenticated request to approve one pending CLI link session."""

    approve_token: str = Field(..., min_length=8, max_length=255)
    device_name: str | None = Field(default=None, max_length=255)


class CliLinkApproveResponse(BaseModel):
    """Approval response after issuing a CLI API key."""

    session_id: str
    status: CliLinkStatus
    key_prefix: str
    expires_at: UTCDateTime


class CliLinkPollResponse(BaseModel):
    """Polling response for a CLI waiting on mobile approval."""

    session_id: str
    status: CliLinkStatus
    expires_at: UTCDateTime
    api_key: str | None = None
    key_prefix: str | None = None
