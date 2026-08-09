"""Queue gateway for task orchestration boundaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.contracts import TaskQueue, TaskType
from app.services.queue import QueueService, TaskEnqueueRequest, get_queue_service


class TaskQueueGateway:
    """Thin facade over QueueService for workflow orchestration."""

    def __init__(self, queue_service: QueueService | None = None) -> None:
        self._queue_service = queue_service or get_queue_service()

    def enqueue(
        self,
        task_type: TaskType,
        *,
        content_id: int | None = None,
        payload: dict[str, Any] | None = None,
        queue_name: TaskQueue | str | None = None,
        dedupe: bool | None = None,
        dedupe_key: str | None = None,
        owner_user_id: int | None = None,
        access_user_id: int | None = None,
        available_at: datetime | None = None,
    ) -> int:
        """Enqueue task with optional dedupe and queue override."""
        enqueue_kwargs: dict[str, Any] = {"task_type": task_type}
        optional_arguments = {
            "content_id": content_id,
            "payload": payload,
            "queue_name": queue_name,
            "dedupe": dedupe,
            "dedupe_key": dedupe_key,
            "owner_user_id": owner_user_id,
            "access_user_id": access_user_id,
            "available_at": available_at,
        }
        enqueue_kwargs.update(
            {key: value for key, value in optional_arguments.items() if value is not None}
        )
        return self._queue_service.enqueue(**enqueue_kwargs)

    def enqueue_many_in_session(
        self,
        db: Session,
        requests: list[TaskEnqueueRequest],
    ) -> list[int]:
        """Enqueue a task batch in the caller-owned transaction."""
        return self._queue_service.enqueue_many_in_session(db, requests)

    def grant_access_in_session(self, db: Session, *, task_id: int, user_id: int) -> None:
        """Grant an active user access to an existing asynchronous job."""

        self._queue_service.grant_access_in_session(db, task_id=task_id, user_id=user_id)


_task_queue_gateway: TaskQueueGateway | None = None


def get_task_queue_gateway() -> TaskQueueGateway:
    """Return a cached queue gateway."""
    global _task_queue_gateway
    if _task_queue_gateway is None:
        _task_queue_gateway = TaskQueueGateway()
    return _task_queue_gateway
