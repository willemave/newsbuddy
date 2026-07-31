"""Queue gateway for task orchestration boundaries."""

from __future__ import annotations

from typing import Any

from app.models.contracts import TaskQueue, TaskType
from app.services.queue import QueueService, get_queue_service


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
    ) -> int:
        """Enqueue task with optional dedupe and queue override."""
        enqueue_kwargs: dict[str, Any] = {"task_type": task_type}
        if content_id is not None:
            enqueue_kwargs["content_id"] = content_id
        if payload is not None:
            enqueue_kwargs["payload"] = payload
        if queue_name is not None:
            enqueue_kwargs["queue_name"] = queue_name
        if dedupe is not None:
            enqueue_kwargs["dedupe"] = dedupe
        if dedupe_key is not None:
            enqueue_kwargs["dedupe_key"] = dedupe_key
        return self._queue_service.enqueue(**enqueue_kwargs)


_task_queue_gateway: TaskQueueGateway | None = None


def get_task_queue_gateway() -> TaskQueueGateway:
    """Return a cached queue gateway."""
    global _task_queue_gateway
    if _task_queue_gateway is None:
        _task_queue_gateway = TaskQueueGateway()
    return _task_queue_gateway
