"""Task models for the sequential pipeline processor."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.contracts import TaskType
from app.models.internal.queue import ClaimedTask, TaskResult
from app.pipeline.retry_policy import retry_will_be_scheduled

__all__ = ["TaskEnvelope", "TaskResult", "task_will_retry"]


class TaskEnvelope(BaseModel):
    """Handler-facing task data derived from an immutable queue claim."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    id: int
    owner_user_id: int | None = None
    task_type: TaskType
    content_id: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0
    created_at: datetime | None = None

    @field_validator("payload", mode="before")
    @classmethod
    def normalize_payload(cls, value: Any) -> dict[str, Any]:
        """Normalize payload to a dictionary."""
        if isinstance(value, dict):
            return value
        if value is None:
            return {}
        raise ValueError("Task payload must be a JSON object")

    @classmethod
    def from_claim(cls, claim: ClaimedTask) -> TaskEnvelope:
        """Build handler input from an ownership-validated queue claim."""
        return cls.model_validate(claim)


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
