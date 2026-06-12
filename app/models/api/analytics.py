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
    ContentInteractionType,
    ContentStatus,
    ContentType,
    OperationStatus,
    SummaryKind,
    SummaryVersion,
)


class RecordContentInteractionRequest(BaseModel):
    """Request to record a user interaction with content."""

    interaction_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Client-generated interaction UUID for idempotency",
    )
    content_id: int = Field(..., gt=0, description="Content ID to associate with the interaction")
    interaction_type: ContentInteractionType = Field(
        ...,
        description="Interaction type. V1 supports opened.",
    )
    occurred_at: UTCDateTime | None = Field(
        None,
        description="Optional ISO timestamp of when interaction occurred",
    )
    surface: str | None = Field(
        None,
        max_length=64,
        description="Surface identifier (e.g., ios_content_detail)",
    )
    context_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured metadata for analytics",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "interaction_id": "c5d968d3-5608-48b4-9838-cb9e5f63f8ae",
                "content_id": 123,
                "interaction_type": "opened",
                "occurred_at": "2026-02-15T09:30:00Z",
                "surface": "ios_content_detail",
                "context_data": {
                    "content_type": "article",
                    "was_read_when_loaded": False,
                },
            }
        }
    )


class RecordContentInteractionResponse(BaseModel):
    """Response after recording a user interaction."""

    status: OperationStatus = Field(..., description="Operation status")
    recorded: bool = Field(
        ...,
        description="True when a new row was inserted; false when idempotent duplicate",
    )
    interaction_id: str = Field(..., description="Echoed client interaction ID")
    analytics_interaction_id: int | None = Field(
        None,
        description="Primary key of recorded analytics row",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "recorded": True,
                "interaction_id": "c5d968d3-5608-48b4-9838-cb9e5f63f8ae",
                "analytics_interaction_id": 456,
            }
        }
    )
