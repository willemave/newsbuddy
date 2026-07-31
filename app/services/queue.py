import time
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_

from app.core.db import get_db
from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.core.settings import get_settings
from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.db import ProcessingTask
from app.models.internal.queue import ClaimedTask, TaskResult, TaskTransition
from app.pipeline.retry_policy import retry_will_be_scheduled
from app.pipeline.task_specs import TASK_SPECS
from app.repositories.processing_task_queue_repository import (
    FinalizationOutcome,
    ProcessingTaskQueueRepository,
)
from app.services.queue_enqueue import (
    QueueEnqueueMixin,
    TaskEnqueueRequest,
    build_task_dedupe_key,
)
from app.services.queue_metrics import (
    get_backpressure_status,
    get_queue_stats,
)
from app.services.queue_retention import (
    DEFAULT_TASK_CLEANUP_BATCH_SIZE,
    DEFAULT_TASK_CLEANUP_MAX_DELETE,
    DEFAULT_TASK_RETENTION_DAYS,
    build_terminal_task_retention_filter,
    cleanup_terminal_tasks_in_session,
)

logger = get_logger(__name__)

__all__ = [
    "DEFAULT_TASK_CLEANUP_BATCH_SIZE",
    "DEFAULT_TASK_CLEANUP_MAX_DELETE",
    "DEFAULT_TASK_RETENTION_DAYS",
    "QueueService",
    "TASK_QUEUE_BY_TYPE",
    "TaskEnqueueRequest",
    "build_task_dedupe_key",
    "build_task_queue_mismatch_filter",
    "build_terminal_task_retention_filter",
    "cleanup_terminal_tasks_in_session",
    "get_queue_service",
]


TASK_QUEUE_BY_TYPE: dict[TaskType, TaskQueue] = {
    task_type: task_spec.queue for task_type, task_spec in TASK_SPECS.items()
}
TASK_QUEUE_VALUE_BY_TYPE: dict[str, str] = {
    task_type.value: queue.value for task_type, queue in TASK_QUEUE_BY_TYPE.items()
}
RETRY_BUCKET_CACHE_SECONDS = 5.0


def _task_lease_seconds() -> int:
    """Return the default worker lease duration in seconds."""
    settings = get_settings()
    return settings.queue.worker_timeout_seconds


def build_task_queue_mismatch_filter(task_type: TaskType | str | None = None):
    """Return a SQL filter for rows whose queue no longer matches task specs."""
    expected_queues = TASK_QUEUE_VALUE_BY_TYPE
    if task_type:
        task_type_value = task_type.value if isinstance(task_type, TaskType) else task_type
        expected_queue = expected_queues.get(task_type_value)
        if expected_queue is None:
            raise ValueError(f"No queue spec found for task type: {task_type_value}")
        expected_queues = {task_type_value: expected_queue}

    return or_(
        *[
            and_(
                ProcessingTask.task_type == task_type_value,
                or_(
                    ProcessingTask.queue_name.is_(None),
                    ProcessingTask.queue_name != expected_queue,
                ),
            )
            for task_type_value, expected_queue in expected_queues.items()
        ]
    )


def _log_dequeued_task(task: ClaimedTask, *, worker_id: str) -> None:
    """Emit the standard log for a claimed task."""
    logger.debug(
        "Task dequeued",
        extra=build_log_extra(
            component="queue",
            operation="dequeue",
            event_name="task.dequeued",
            status="started",
            task_id=task.id,
            task_type=task.task_type,
            queue_name=task.queue_name,
            worker_id=worker_id,
            content_id=task.content_id,
            context_data={
                "retry_count": task.retry_count,
                "lease_token": str(task.lease_token),
                "lease_expires_at": task.lease_expires_at.isoformat(),
            },
        ),
    )


class QueueService(QueueEnqueueMixin):
    """Simple database-backed task queue."""

    def __init__(self, repository: ProcessingTaskQueueRepository | None = None) -> None:
        self._repository = repository or ProcessingTaskQueueRepository()
        # Cursor used for best-effort rotation across retry buckets.
        # Keyed by (queue_name, task_type) so busy queues do not starve retries.
        self._retry_bucket_cursor: dict[tuple[str | None, str | None], int] = {}
        self._retry_bucket_cache: dict[tuple[str | None, str | None], tuple[float, list[int]]] = {}

    def _queue_db(self):
        """Keep the historical DB patch seam for callers and tests."""
        return get_db()

    def _ordered_retry_counts(
        self,
        available_retry_counts: list[int],
        cursor_key: tuple[str | None, str | None],
    ) -> list[int]:
        """Return retry buckets in a rotating order to reduce starvation."""
        if not available_retry_counts:
            return []
        if len(available_retry_counts) == 1:
            return available_retry_counts

        cursor = self._retry_bucket_cursor.get(cursor_key, 0)
        ordered = available_retry_counts[cursor:] + available_retry_counts[:cursor]
        self._retry_bucket_cursor[cursor_key] = (cursor + 1) % len(available_retry_counts)
        return ordered

    def _available_retry_counts(
        self,
        *,
        task_type: str | None,
        queue_name: str | None,
        cursor_key: tuple[str | None, str | None],
        use_cache: bool,
    ) -> list[int]:
        """Return claimable retry buckets, caching briefly to avoid per-claim scans."""
        now_monotonic = time.monotonic()
        if use_cache:
            cached = self._retry_bucket_cache.get(cursor_key)
            if cached is not None:
                expires_at, retry_counts = cached
                if expires_at > now_monotonic:
                    return retry_counts

        retry_counts = self._repository.list_claimable_retry_counts(
            task_type=task_type,
            queue_name=queue_name,
        )
        if retry_counts:
            self._retry_bucket_cache[cursor_key] = (
                now_monotonic + RETRY_BUCKET_CACHE_SECONDS,
                retry_counts,
            )
        else:
            self._retry_bucket_cache.pop(cursor_key, None)
        return retry_counts

    def dequeue(
        self,
        task_type: TaskType | None = None,
        worker_id: str = "worker",
        queue_name: TaskQueue | str | None = None,
    ) -> ClaimedTask | None:
        """
        Get the next available task from the queue.

        Args:
            task_type: Filter by task type (optional)
            worker_id: ID of the worker claiming the task
            queue_name: Filter by queue partition (optional)

        Returns:
            A validated owned task or None if the queue is empty
        """
        normalized_queue = self._normalize_queue_name(queue_name)
        normalized_task_type = task_type.value if task_type is not None else None
        cursor_key = (normalized_queue, normalized_task_type)
        for use_cache in (True, False):
            available_retry_counts = self._available_retry_counts(
                task_type=normalized_task_type,
                queue_name=normalized_queue,
                cursor_key=cursor_key,
                use_cache=use_cache,
            )
            if not available_retry_counts:
                return None

            for selected_retry in self._ordered_retry_counts(
                available_retry_counts,
                cursor_key,
            ):
                task = self._repository.claim_task(
                    lease_seconds=_task_lease_seconds(),
                    worker_id=worker_id,
                    retry_count=selected_retry,
                    task_type=normalized_task_type,
                    queue_name=normalized_queue,
                )
                if task is None:
                    continue
                _log_dequeued_task(task, worker_id=worker_id)
                return task

            self._retry_bucket_cache.pop(cursor_key, None)

        return None

    def renew_lease(
        self,
        claim: ClaimedTask,
        *,
        lease_seconds: int | None = None,
    ) -> bool:
        """Extend an unexpired lease for the exact worker claim that owns it."""
        effective_lease_seconds = _task_lease_seconds() if lease_seconds is None else lease_seconds
        return self._repository.renew_lease(
            claim,
            lease_seconds=effective_lease_seconds,
        )

    def finalize_task(
        self,
        claim: ClaimedTask,
        result: TaskResult,
        *,
        max_retries: int = 3,
    ) -> TaskTransition | None:
        """Persist one ownership-checked terminal, retry, or deferral transition."""
        if result.deferred and result.success:
            raise ValueError("A successful task cannot also be deferred")

        should_retry = not result.deferred and retry_will_be_scheduled(
            success=result.success,
            retryable=result.retryable,
            retry_count=claim.retry_count,
            max_retries=max_retries,
        )
        resolved_delay_seconds = None
        if result.deferred:
            resolved_delay_seconds = max(result.retry_delay_seconds or 0, 0)
        elif should_retry:
            resolved_delay_seconds = max(
                result.retry_delay_seconds
                if result.retry_delay_seconds is not None
                else min(60 * (2**claim.retry_count), 3600),
                0,
            )
        resolved_error = None
        if not result.success and not result.deferred:
            resolved_error = result.error_message or "Task failed without error details"

        if result.success:
            outcome = FinalizationOutcome.SUCCEEDED
        elif result.deferred:
            outcome = FinalizationOutcome.DEFERRED
        elif should_retry:
            outcome = FinalizationOutcome.RETRY
        else:
            outcome = FinalizationOutcome.FAILED

        transition = self._repository.finalize_task(
            claim,
            outcome=outcome,
            error_message=resolved_error,
            retry_delay_seconds=resolved_delay_seconds,
        )
        if transition is None:
            logger.warning(
                "Task finalization rejected because lease ownership was lost",
                extra=build_log_extra(
                    component="queue",
                    operation="finalize_task",
                    event_name="task.finalization_rejected",
                    status="degraded",
                    task_id=claim.id,
                    worker_id=claim.locked_by,
                    context_data={
                        "failure_class": "LeaseOwnershipLost",
                        "lease_token": str(claim.lease_token),
                    },
                ),
            )
            return None

        if transition.status is TaskStatus.COMPLETED:
            logger.info(
                "Task completed",
                extra=build_log_extra(
                    component="queue",
                    operation="finalize_task",
                    event_name="task.completed",
                    status="completed",
                    task_id=claim.id,
                    task_type=transition.task_type,
                    queue_name=transition.queue_name,
                    worker_id=claim.locked_by,
                    content_id=transition.content_id,
                ),
            )
        elif transition.status is TaskStatus.PENDING:
            logger.info(
                "Task deferred" if transition.deferred else "Task retry scheduled",
                extra=build_log_extra(
                    component="queue",
                    operation="finalize_task",
                    event_name="task.deferred" if transition.deferred else "task.retry_scheduled",
                    status="deferred" if transition.deferred else "retry_scheduled",
                    task_id=claim.id,
                    task_type=transition.task_type,
                    queue_name=transition.queue_name,
                    worker_id=claim.locked_by,
                    content_id=transition.content_id,
                    context_data={
                        "retry_count": transition.retry_count,
                        "delay_seconds": transition.retry_delay_seconds,
                        "error_message": transition.error_message,
                    },
                ),
            )
        else:
            logger.error(
                "Task failed",
                extra=build_log_extra(
                    component="queue",
                    operation="finalize_task",
                    event_name="task.failed",
                    status="failed",
                    item_id=claim.id,
                    task_id=claim.id,
                    task_type=transition.task_type,
                    queue_name=transition.queue_name,
                    worker_id=claim.locked_by,
                    content_id=transition.content_id,
                    context_data={"error_message": transition.error_message},
                ),
            )

        return transition

    def cleanup_terminal_tasks(
        self,
        *,
        retention_days: int = DEFAULT_TASK_RETENTION_DAYS,
        batch_size: int = DEFAULT_TASK_CLEANUP_BATCH_SIZE,
        max_delete: int = DEFAULT_TASK_CLEANUP_MAX_DELETE,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Delete expired terminal tasks in bounded, separately committed batches."""
        with get_db() as db:
            result = cleanup_terminal_tasks_in_session(
                db,
                retention_days=retention_days,
                batch_size=batch_size,
                max_delete=max_delete,
                now=now,
            )
        logger.info(
            "Cleaned up terminal processing tasks",
            extra=build_log_extra(
                component="queue",
                operation="cleanup_terminal_tasks",
                event_name="task.cleanup_completed",
                status="completed",
                context_data={
                    "deleted_count": result["deleted_count"],
                    "batch_count": result["batch_count"],
                    "batch_size": result["batch_size"],
                    "max_delete": result["max_delete"],
                    "has_more": result["has_more"],
                    "retention_days": result["retention_days"],
                    "cutoff": result["cutoff"].isoformat(),
                },
            ),
        )
        return result

    def get_queue_stats(self) -> dict[str, Any]:
        with get_db() as db:
            return get_queue_stats(db)

    def get_backpressure_status(self) -> dict[str, Any]:
        with get_db() as db:
            return get_backpressure_status(db, get_settings().queue)


# Global instance
_queue_service = None


def get_queue_service() -> QueueService:
    """Get the global queue service instance."""
    global _queue_service
    if _queue_service is None:
        _queue_service = QueueService()
    return _queue_service
