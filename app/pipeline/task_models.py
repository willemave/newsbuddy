"""Task models for the sequential pipeline processor."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.contracts import TaskType
from app.models.internal.queue import ClaimedTask
from app.pipeline.retry_policy import retry_will_be_scheduled


class TaskEnvelope(BaseModel):
    """Normalized task payload from the queue."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    id: int
    task_type: TaskType
    content_id: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0
    status: str | None = None
    queue_name: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    locked_by: str | None = None
    lease_token: UUID | None = None
    lease_expires_at: datetime | None = None

    @field_validator("payload", mode="before")
    @classmethod
    def normalize_payload(cls, value: Any) -> dict[str, Any]:
        """Normalize payload to a dictionary."""
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        return {}

    @classmethod
    def from_queue_data(
        cls,
        task_data: ClaimedTask | Mapping[str, Any],
    ) -> TaskEnvelope:
        """Build an execution envelope from a claimed task or test mapping."""
        return cls.model_validate(task_data)


class TaskResult(BaseModel):
    """Outcome for task processing."""

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


def task_will_retry(
    result: TaskResult,
    *,
    retry_count: int,
    max_retries: int,
) -> bool:
    """Return whether the queue will schedule another attempt for this result."""

    if result.deferred:
        return False
    return retry_will_be_scheduled(
        success=result.success,
        retryable=result.retryable,
        retry_count=retry_count,
        max_retries=max_retries,
    )
