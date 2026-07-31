"""Validated internal contracts for processing-task queue coordination."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.contracts import TaskStatus


class ClaimedTask(BaseModel):
    """One processing task owned by an exact worker claim attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int
    task_type: str
    content_id: int | None
    payload: dict[str, Any]
    retry_count: int = Field(ge=0, strict=True)
    status: Literal["processing"]
    queue_name: str
    created_at: datetime | None
    available_at: datetime
    started_at: datetime
    locked_at: datetime
    locked_by: str = Field(min_length=1)
    lease_token: UUID
    lease_expires_at: datetime

    @field_validator("payload", mode="before")
    @classmethod
    def normalize_payload(cls, value: Any) -> dict[str, Any]:
        """Preserve the historical empty-payload behavior for nullable JSON rows."""
        return value if isinstance(value, dict) else {}


class TaskTransition(BaseModel):
    """Persisted result of one ownership-checked task finalization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_type: str
    queue_name: str
    content_id: int | None
    error_message: str | None
    status: TaskStatus
    retry_count: int = Field(ge=0, strict=True)
    retry_delay_seconds: int | None
    deferred: bool
    available_at: datetime
