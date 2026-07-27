import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update

from app.core.db import get_db
from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.core.settings import get_settings
from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.db import ProcessingTask
from app.pipeline.retry_policy import retry_will_be_scheduled
from app.pipeline.task_specs import TASK_SPECS
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


def _utc_now() -> datetime:
    """Return the repo's normalized naive-UTC timestamp shape."""
    return datetime.now(UTC).replace(tzinfo=None)


def _task_lease_seconds() -> int:
    """Return the default worker lease duration in seconds."""
    settings = get_settings()
    return max(int(settings.queue.worker_timeout_seconds), 1)


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


def _log_dequeued_task(task_data: dict[str, Any], *, worker_id: str) -> None:
    """Emit the standard log for a claimed task."""
    logger.debug(
        "Task dequeued",
        extra=build_log_extra(
            component="queue",
            operation="dequeue",
            event_name="task.dequeued",
            status="started",
            task_id=task_data["id"],
            task_type=task_data["task_type"],
            queue_name=task_data["queue_name"],
            worker_id=worker_id,
            content_id=task_data["content_id"],
            context_data={
                "retry_count": task_data["retry_count"],
                "lease_expires_at": task_data["lease_expires_at"].isoformat()
                if task_data["lease_expires_at"] is not None
                else None,
            },
        ),
    )


def _clear_task_lease(task: ProcessingTask) -> None:
    """Clear lease ownership fields on a task row."""
    task.locked_at = None
    task.locked_by = None
    task.lease_expires_at = None


def _claimable_task_filters(now: datetime):
    """Return the predicate for tasks that are ready to be claimed."""
    return or_(
        and_(
            ProcessingTask.status == TaskStatus.PENDING.value,
            ProcessingTask.available_at <= now,
        ),
        and_(
            ProcessingTask.status == TaskStatus.PROCESSING.value,
            ProcessingTask.lease_expires_at.is_not(None),
            ProcessingTask.lease_expires_at <= now,
        ),
    )


class QueueService(QueueEnqueueMixin):
    """Simple database-backed task queue."""

    def __init__(self) -> None:
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
        db,
        *,
        base_filters: list[Any],
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

        retry_rows = (
            db.query(ProcessingTask.retry_count.label("retry_count"))
            .filter(*base_filters)
            .distinct()
            .order_by("retry_count")
            .all()
        )
        retry_counts = [int(row.retry_count) for row in retry_rows]
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
    ) -> dict[str, Any] | None:
        """
        Get the next available task from the queue.

        Args:
            task_type: Filter by task type (optional)
            worker_id: ID of the worker claiming the task
            queue_name: Filter by queue partition (optional)

        Returns:
            Task data as dictionary or None if queue is empty
        """
        with get_db() as db:
            now = _utc_now()
            normalized_queue = self._normalize_queue_name(queue_name)
            task_order = ProcessingTask.available_at
            base_filters = [_claimable_task_filters(now)]
            if task_type:
                base_filters.append(ProcessingTask.task_type == task_type.value)
            if normalized_queue:
                base_filters.append(ProcessingTask.queue_name == normalized_queue)

            cursor_key = (
                normalized_queue,
                task_type.value if task_type is not None else None,
            )
            for use_cache in (True, False):
                available_retry_counts = self._available_retry_counts(
                    db,
                    base_filters=base_filters,
                    cursor_key=cursor_key,
                    use_cache=use_cache,
                )
                if not available_retry_counts:
                    return None

                for selected_retry in self._ordered_retry_counts(
                    available_retry_counts,
                    cursor_key,
                ):
                    candidate_id_subquery = (
                        select(ProcessingTask.id)
                        .where(
                            *base_filters,
                            ProcessingTask.retry_count == selected_retry,
                        )
                        .order_by(
                            task_order.asc(),
                            ProcessingTask.created_at.asc(),
                            ProcessingTask.id.asc(),
                        )
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                    claim_stmt = (
                        update(ProcessingTask)
                        .where(ProcessingTask.id == candidate_id_subquery.scalar_subquery())
                        .values(
                            status=TaskStatus.PROCESSING.value,
                            started_at=now,
                            locked_at=now,
                            locked_by=worker_id,
                            lease_expires_at=now + timedelta(seconds=_task_lease_seconds()),
                        )
                        .returning(
                            ProcessingTask.id,
                            ProcessingTask.task_type,
                            ProcessingTask.content_id,
                            ProcessingTask.payload,
                            ProcessingTask.retry_count,
                            ProcessingTask.status,
                            ProcessingTask.queue_name,
                            ProcessingTask.created_at,
                            ProcessingTask.available_at,
                            ProcessingTask.started_at,
                            ProcessingTask.completed_at,
                            ProcessingTask.locked_at,
                            ProcessingTask.locked_by,
                            ProcessingTask.lease_expires_at,
                        )
                    )
                    task_row = db.execute(claim_stmt).mappings().first()
                    if task_row is None:
                        continue
                    task_data = dict(task_row)
                    task_data["retry_count"] = int(task_data["retry_count"])
                    _log_dequeued_task(task_data, worker_id=worker_id)
                    return task_data

                self._retry_bucket_cache.pop(cursor_key, None)

            return None

    def renew_lease(
        self,
        task_id: int,
        *,
        worker_id: str,
        lease_seconds: int | None = None,
    ) -> bool:
        """Extend the lease for a task currently owned by the worker."""
        effective_lease_seconds = max(int(lease_seconds or _task_lease_seconds()), 1)
        with get_db() as db:
            now = _utc_now()
            renewed = (
                db.query(ProcessingTask)
                .filter(ProcessingTask.id == task_id)
                .filter(ProcessingTask.status == TaskStatus.PROCESSING.value)
                .filter(ProcessingTask.locked_by == worker_id)
                .update(
                    {
                        ProcessingTask.locked_at: now,
                        ProcessingTask.lease_expires_at: now
                        + timedelta(seconds=effective_lease_seconds),
                    },
                    synchronize_session=False,
                )
            )
            return bool(renewed)

    def complete_task(self, task_id: int, success: bool = True, error_message: str | None = None):
        """Mark a task as completed."""
        with get_db() as db:
            task = db.query(ProcessingTask).filter(ProcessingTask.id == task_id).first()
            if not task:
                completion = None
            else:
                task.completed_at = _utc_now()
                _clear_task_lease(task)
                if success:
                    task.status = TaskStatus.COMPLETED.value
                    task.error_message = None
                else:
                    task.status = TaskStatus.FAILED.value
                    task.error_message = error_message or "Task failed without error details"
                completion = {
                    "task_type": task.task_type,
                    "queue_name": task.queue_name,
                    "content_id": task.content_id,
                    "error_message": task.error_message,
                }

            if completion is None:
                logger.error(
                    "Task not found",
                    extra=build_log_extra(
                        component="queue",
                        operation="complete_task",
                        event_name="task.failed",
                        status="failed",
                        task_id=task_id,
                        context_data={"failure_class": "TaskNotFound"},
                    ),
                )
                return

            if success:
                logger.info(
                    "Task completed",
                    extra=build_log_extra(
                        component="queue",
                        operation="complete_task",
                        event_name="task.completed",
                        status="completed",
                        task_id=task_id,
                        task_type=completion["task_type"],
                        queue_name=completion["queue_name"],
                        content_id=completion["content_id"],
                    ),
                )
            else:
                logger.error(
                    "Task failed",
                    extra=build_log_extra(
                        component="queue",
                        operation="complete_task",
                        event_name="task.failed",
                        status="failed",
                        item_id=task_id,
                        task_id=task_id,
                        task_type=completion["task_type"],
                        queue_name=completion["queue_name"],
                        content_id=completion["content_id"],
                        context_data={"error_message": completion["error_message"]},
                    ),
                )

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
        """Persist one terminal or retry transition for a processed task."""
        with get_db() as db:
            should_retry = not deferred and retry_will_be_scheduled(
                success=success,
                retryable=retryable,
                retry_count=current_retry_count,
                max_retries=max_retries,
            )
            resolved_delay_seconds = retry_delay_seconds if should_retry or deferred else None
            task = db.query(ProcessingTask).filter(ProcessingTask.id == task_id).first()
            if not task:
                transition = None
            else:
                now = _utc_now()
                persisted_retry_count = int(task.retry_count or 0)
                base_retry_count = max(persisted_retry_count, int(current_retry_count or 0))

                if success:
                    task.status = TaskStatus.COMPLETED.value
                    task.completed_at = now
                    task.error_message = None
                elif deferred:
                    task.status = TaskStatus.PENDING.value
                    task.retry_count = base_retry_count
                    task.started_at = None
                    task.completed_at = None
                    task.available_at = now + timedelta(seconds=resolved_delay_seconds or 0)
                    task.error_message = None
                elif should_retry:
                    task.status = TaskStatus.PENDING.value
                    task.retry_count = base_retry_count + 1
                    task.started_at = None
                    task.completed_at = None
                    task.available_at = now + timedelta(seconds=resolved_delay_seconds or 0)
                    task.error_message = error_message or "Task failed without error details"
                else:
                    task.status = TaskStatus.FAILED.value
                    task.completed_at = now
                    task.error_message = error_message or "Task failed without error details"

                _clear_task_lease(task)
                transition = {
                    "task_type": task.task_type,
                    "queue_name": task.queue_name,
                    "content_id": task.content_id,
                    "error_message": task.error_message,
                    "status": task.status,
                    "retry_count": int(task.retry_count or 0),
                    "retry_delay_seconds": resolved_delay_seconds,
                    "deferred": deferred,
                    "available_at": task.available_at,
                }

            if transition is None:
                logger.error(
                    "Task not found",
                    extra=build_log_extra(
                        component="queue",
                        operation="finalize_task",
                        event_name="task.failed",
                        status="failed",
                        task_id=task_id,
                        context_data={"failure_class": "TaskNotFound"},
                    ),
                )
                return None

            if transition["status"] == TaskStatus.COMPLETED.value:
                logger.info(
                    "Task completed",
                    extra=build_log_extra(
                        component="queue",
                        operation="finalize_task",
                        event_name="task.completed",
                        status="completed",
                        task_id=task_id,
                        task_type=transition["task_type"],
                        queue_name=transition["queue_name"],
                        content_id=transition["content_id"],
                    ),
                )
            elif transition["status"] == TaskStatus.PENDING.value:
                logger.info(
                    "Task deferred" if transition["deferred"] else "Task retry scheduled",
                    extra=build_log_extra(
                        component="queue",
                        operation="finalize_task",
                        event_name=(
                            "task.deferred" if transition["deferred"] else "task.retry_scheduled"
                        ),
                        status="deferred" if transition["deferred"] else "retry_scheduled",
                        task_id=task_id,
                        task_type=transition["task_type"],
                        queue_name=transition["queue_name"],
                        content_id=transition["content_id"],
                        context_data={
                            "retry_count": transition["retry_count"],
                            "delay_seconds": transition["retry_delay_seconds"],
                            "error_message": transition["error_message"],
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
                        item_id=task_id,
                        task_id=task_id,
                        task_type=transition["task_type"],
                        queue_name=transition["queue_name"],
                        content_id=transition["content_id"],
                        context_data={"error_message": transition["error_message"]},
                    ),
                )

            return transition

    def retry_task(self, task_id: int, delay_seconds: int = 60):
        """Retry a failed task after a delay."""
        with get_db() as db:
            task = db.query(ProcessingTask).filter(ProcessingTask.id == task_id).first()
            if not task:
                retry_result = None
            else:
                task.status = TaskStatus.PENDING.value
                task.retry_count = int(task.retry_count or 0) + 1
                task.started_at = None
                task.completed_at = None
                task.available_at = _utc_now() + timedelta(seconds=delay_seconds)
                _clear_task_lease(task)
                retry_result = {
                    "task_type": task.task_type,
                    "queue_name": task.queue_name,
                    "content_id": task.content_id,
                    "retry_count": task.retry_count,
                    "available_at": task.available_at,
                }

            if retry_result is None:
                logger.error(
                    "Task not found",
                    extra=build_log_extra(
                        component="queue",
                        operation="retry_task",
                        event_name="task.retry_scheduled",
                        status="failed",
                        task_id=task_id,
                        context_data={"failure_class": "TaskNotFound"},
                    ),
                )
                return

            logger.info(
                "Task retry scheduled",
                extra=build_log_extra(
                    component="queue",
                    operation="retry_task",
                    event_name="task.retry_scheduled",
                    status="retry_scheduled",
                    task_id=task_id,
                    task_type=retry_result["task_type"],
                    queue_name=retry_result["queue_name"],
                    content_id=retry_result["content_id"],
                    context_data={
                        "retry_count": retry_result["retry_count"],
                        "delay_seconds": delay_seconds,
                    },
                ),
            )

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
