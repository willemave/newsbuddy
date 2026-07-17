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

    def complete_task(
        self,
        task_id: int,
        success: bool = True,
        error_message: str | None = None,
    ) -> None:
        """Mark task complete/failed."""
        self._queue_service.complete_task(task_id, success=success, error_message=error_message)

    def retry_task(self, task_id: int, delay_seconds: int = 60) -> None:
        """Retry task after delay."""
        self._queue_service.retry_task(task_id, delay_seconds=delay_seconds)

    def finalize_task(
        self,
        task_id: int,
        *,
        success: bool,
        error_message: str | None = None,
        retryable: bool = True,
        current_retry_count: int = 0,
        max_retries: int = 3,
        retry_delay_seconds: int | None = None,
        deferred: bool = False,
    ) -> dict[str, Any] | None:
        """Apply one final task transition after processing."""
        return self._queue_service.finalize_task(
            task_id,
            success=success,
            error_message=error_message,
            retryable=retryable,
            current_retry_count=current_retry_count,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            deferred=deferred,
        )

    def dequeue(
        self,
        *,
        task_type: TaskType | None = None,
        worker_id: str = "worker",
        queue_name: TaskQueue | str | None = None,
    ) -> dict[str, Any] | None:
        """Dequeue next task."""
        return self._queue_service.dequeue(
            task_type=task_type,
            worker_id=worker_id,
            queue_name=queue_name,
        )

    def get_queue_stats(self) -> dict[str, Any]:
        """Return queue stats."""
        return self._queue_service.get_queue_stats()


_task_queue_gateway: TaskQueueGateway | None = None


def get_task_queue_gateway() -> TaskQueueGateway:
    """Return a cached queue gateway."""
    global _task_queue_gateway
    if _task_queue_gateway is None:
        _task_queue_gateway = TaskQueueGateway()
    return _task_queue_gateway
