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


class ApiKeySummaryResponse(BaseModel):
    """Admin-facing summary for a user API key."""

    id: int
    user_id: int
    key_prefix: str
    created_at: datetime
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
    created_by_admin_user_id: int | None = None


class ApiKeyCreateResponse(BaseModel):
    """Admin response that reveals a newly created API key once."""

    api_key: str
    key: str
    key_prefix: str
    record: ApiKeySummaryResponse
