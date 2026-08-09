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
    owner_user_id: int | None = None
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
        if isinstance(value, dict):
            return value
        if value is None:
            return {}
        raise ValueError("Claimed task payload must be a JSON object")


class TaskResult(BaseModel):
    """Typed outcome returned by a processing-task handler."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    success: bool
    error_message: str | None = None
    retry_delay_seconds: int | None = None
    retryable: bool = True
    deferred: bool = False

    @classmethod
    def ok(cls) -> TaskResult:
        """Return a successful task result."""
        return cls(success=True)

    @classmethod
    def fail(
        cls,
        error_message: str | None = None,
        *,
        retryable: bool = True,
        retry_delay_seconds: int | None = None,
    ) -> TaskResult:
        """Return a failed task result."""
        return cls(
            success=False,
            error_message=error_message,
            retryable=retryable,
            retry_delay_seconds=retry_delay_seconds,
        )

    @classmethod
    def defer(
        cls,
        *,
        retry_delay_seconds: int,
    ) -> TaskResult:
        """Return a pending outcome that does not consume the retry budget."""
        return cls(
            success=False,
            retryable=False,
            deferred=True,
            retry_delay_seconds=retry_delay_seconds,
        )


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
