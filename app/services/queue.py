import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from app.core.db import get_db
from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.core.settings import get_settings
from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.schema import ProcessingTask

logger = get_logger(__name__)


TASK_QUEUE_BY_TYPE: dict[TaskType, TaskQueue] = {
    TaskType.SCRAPE: TaskQueue.CONTENT,
    TaskType.BACKFILL_FEEDS: TaskQueue.ONBOARDING,
    TaskType.ANALYZE_URL: TaskQueue.CONTENT,
    TaskType.PROCESS_CONTENT: TaskQueue.CONTENT,
    TaskType.ENRICH_NEWS_ITEM_ARTICLE: TaskQueue.CONTENT,
    TaskType.PROCESS_NEWS_ITEM: TaskQueue.CONTENT,
    TaskType.PROCESS_PODCAST_MEDIA: TaskQueue.MEDIA,
    TaskType.DOWNLOAD_AUDIO: TaskQueue.MEDIA,
    TaskType.TRANSCRIBE: TaskQueue.MEDIA,
    TaskType.SUMMARIZE: TaskQueue.CONTENT,
    TaskType.FETCH_DISCUSSION: TaskQueue.CONTENT,
    TaskType.GENERATE_IMAGE: TaskQueue.IMAGE,
    TaskType.DISCOVER_FEEDS: TaskQueue.CONTENT,
    TaskType.GENERATE_AGENT_DIGEST: TaskQueue.CONTENT,
    TaskType.ONBOARDING_DISCOVER: TaskQueue.ONBOARDING,
    TaskType.DIG_DEEPER: TaskQueue.CHAT,
    TaskType.SYNC_INTEGRATION: TaskQueue.TWITTER,
    TaskType.GENERATE_INSIGHT_REPORT: TaskQueue.CONTENT,
}

DEDUPABLE_CONTENT_TASK_TYPES: set[TaskType] = {
    TaskType.PROCESS_CONTENT,
    TaskType.PROCESS_PODCAST_MEDIA,
    TaskType.SUMMARIZE,
    TaskType.FETCH_DISCUSSION,
    TaskType.GENERATE_IMAGE,
}
ACTIVE_TASK_STATUSES: tuple[str, str] = (
    TaskStatus.PENDING.value,
    TaskStatus.PROCESSING.value,
)
ACTIVE_DEDUPE_INDEX_WHERE = text("dedupe_key IS NOT NULL AND status IN ('pending', 'processing')")


def _utc_now() -> datetime:
    """Return the repo's normalized naive-UTC timestamp shape."""
    return datetime.now(UTC).replace(tzinfo=None)


def _task_lease_seconds() -> int:
    """Return the default worker lease duration in seconds."""
    settings = get_settings()
    return max(int(settings.worker_timeout_seconds), 1)


def _normalize_payload_for_dedupe(payload: dict[str, Any] | None) -> str | None:
    """Serialize payload to a stable dedupe fragment when needed."""
    if not payload:
        return None
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _build_dedupe_key(
    *,
    task_type: TaskType,
    content_id: int | None,
    payload: dict[str, Any] | None,
    queue_name: str,
    should_dedupe: bool,
) -> str | None:
    """Build a stable dedupe key for active work items."""
    if not should_dedupe:
        return None

    parts = [queue_name, task_type.value]
    if content_id is not None:
        parts.append(f"content:{content_id}")
    payload_fragment = _normalize_payload_for_dedupe(payload)
    if payload_fragment is not None and content_id is None:
        parts.append(f"payload:{payload_fragment}")
    return "|".join(parts)


def _lookup_active_task_by_dedupe_key(db, *, dedupe_key: str, active_task_order):
    """Return the newest active task for one dedupe key."""
    return (
        db.query(ProcessingTask)
        .filter(ProcessingTask.dedupe_key == dedupe_key)
        .filter(ProcessingTask.status.in_(ACTIVE_TASK_STATUSES))
        .order_by(active_task_order.desc(), ProcessingTask.id.desc())
        .first()
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
            or_(
                ProcessingTask.available_at.is_(None),
                ProcessingTask.available_at <= now,
            ),
        ),
        and_(
            ProcessingTask.status == TaskStatus.PROCESSING.value,
            ProcessingTask.lease_expires_at.is_not(None),
            ProcessingTask.lease_expires_at <= now,
        ),
    )


class QueueService:
    """Simple database-backed task queue."""

    def __init__(self) -> None:
        # Cursor used for best-effort rotation across retry buckets.
        # Keyed by (queue_name, task_type) so busy queues do not starve retries.
        self._retry_bucket_cursor: dict[tuple[str | None, str | None], int] = {}

    @staticmethod
    def _normalize_queue_name(
        queue_name: TaskQueue | str | None,
    ) -> str | None:
        """Normalize queue names for DB filtering."""
        if queue_name is None:
            return None
        if isinstance(queue_name, TaskQueue):
            return queue_name.value
        return TaskQueue(queue_name).value

    @staticmethod
    def _resolve_task_queue(
        task_type: TaskType,
        queue_name: TaskQueue | str | None = None,
    ) -> str:
        """Resolve the target queue for a task enqueue."""
        normalized = QueueService._normalize_queue_name(queue_name)
        if normalized:
            return normalized
        return TASK_QUEUE_BY_TYPE[task_type].value

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

    def enqueue(
        self,
        task_type: TaskType,
        content_id: int | None = None,
        payload: dict[str, Any] | None = None,
        queue_name: TaskQueue | str | None = None,
        dedupe: bool | None = None,
        dedupe_key: str | None = None,
    ) -> int:
        """
        Add a task to the queue.

        Returns:
            Task ID
        """
        target_queue = self._resolve_task_queue(task_type, queue_name)
        task_payload = payload or {}
        active_task_order = func.coalesce(
            ProcessingTask.available_at,
            ProcessingTask.created_at,
        )
        with get_db() as db:
            should_dedupe = (
                dedupe if dedupe is not None else task_type in DEDUPABLE_CONTENT_TASK_TYPES
            )
            resolved_dedupe_key = dedupe_key
            if resolved_dedupe_key is None:
                resolved_dedupe_key = _build_dedupe_key(
                    task_type=task_type,
                    content_id=content_id,
                    payload=task_payload,
                    queue_name=target_queue,
                    should_dedupe=should_dedupe,
                )
            if resolved_dedupe_key is not None:
                inserted_task_id = db.execute(
                    postgresql_insert(ProcessingTask)
                    .values(
                        task_type=task_type.value,
                        content_id=content_id,
                        payload=task_payload,
                        status=TaskStatus.PENDING.value,
                        queue_name=target_queue,
                        available_at=_utc_now(),
                        dedupe_key=resolved_dedupe_key,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[ProcessingTask.dedupe_key],
                        index_where=ACTIVE_DEDUPE_INDEX_WHERE,
                    )
                    .returning(ProcessingTask.id)
                ).scalar_one_or_none()
                if inserted_task_id is not None:
                    task_id = int(inserted_task_id)
                    notification_payload = json.dumps(
                        {
                            "task_id": task_id,
                            "task_type": task_type.value,
                            "queue_name": target_queue,
                        },
                        separators=(",", ":"),
                    )
                    db.execute(select(func.pg_notify("processing_tasks", notification_payload)))

                    logger.info(
                        "Task enqueued",
                        extra=build_log_extra(
                            component="queue",
                            operation="enqueue",
                            event_name="task.enqueued",
                            status="completed",
                            task_id=task_id,
                            task_type=task_type.value,
                            queue_name=target_queue,
                            content_id=content_id,
                            context_data={"has_payload": bool(payload)},
                        ),
                    )
                    return task_id

                existing_task = _lookup_active_task_by_dedupe_key(
                    db,
                    dedupe_key=resolved_dedupe_key,
                    active_task_order=active_task_order,
                )
                if existing_task:
                    existing_task_id = existing_task.id
                    if existing_task_id is None:
                        raise ValueError("Existing processing task is missing an id")
                    logger.info(
                        "Reusing existing task",
                        extra=build_log_extra(
                            component="queue",
                            operation="enqueue",
                            event_name="task.reused",
                            status="completed",
                            task_id=int(existing_task_id),
                            task_type=task_type.value,
                            queue_name=target_queue,
                            content_id=content_id,
                        ),
                    )
                    return int(existing_task_id)
                raise RuntimeError(
                    "Task dedupe conflict did not return "
                    "an inserted task or an existing active task"
                )

            task = ProcessingTask(
                task_type=task_type.value,
                content_id=content_id,
                payload=task_payload,
                status=TaskStatus.PENDING.value,
                queue_name=target_queue,
                available_at=_utc_now(),
                dedupe_key=resolved_dedupe_key,
            )
            db.add(task)
            db.flush()
            task_row_id = task.id
            if task_row_id is None:
                raise ValueError("Processing task insert did not produce an id")
            task_id = int(task_row_id)

            notification_payload = json.dumps(
                {
                    "task_id": task_id,
                    "task_type": task_type.value,
                    "queue_name": target_queue,
                },
                separators=(",", ":"),
            )
            db.execute(select(func.pg_notify("processing_tasks", notification_payload)))

            logger.info(
                "Task enqueued",
                extra=build_log_extra(
                    component="queue",
                    operation="enqueue",
                    event_name="task.enqueued",
                    status="completed",
                    task_id=task_id,
                    task_type=task_type.value,
                    queue_name=target_queue,
                    content_id=content_id,
                    context_data={"has_payload": bool(payload)},
                ),
            )
            return task_id

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
            task_order = func.coalesce(ProcessingTask.available_at, ProcessingTask.created_at)
            base_filters = [_claimable_task_filters(now)]
            if task_type:
                base_filters.append(ProcessingTask.task_type == task_type.value)
            if normalized_queue:
                base_filters.append(ProcessingTask.queue_name == normalized_queue)

            retry_rows = (
                db.query(func.coalesce(ProcessingTask.retry_count, 0).label("retry_count"))
                .filter(*base_filters)
                .distinct()
                .order_by("retry_count")
                .all()
            )
            if not retry_rows:
                return None

            available_retry_counts = [int(row.retry_count or 0) for row in retry_rows]
            cursor_key = (
                normalized_queue,
                task_type.value if task_type is not None else None,
            )
            for selected_retry in self._ordered_retry_counts(
                available_retry_counts,
                cursor_key,
            ):
                candidate_id_subquery = (
                    select(ProcessingTask.id)
                    .where(
                        *base_filters,
                        func.coalesce(ProcessingTask.retry_count, 0) == selected_retry,
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
                task_data["retry_count"] = int(task_data.get("retry_count") or 0)
                _log_dequeued_task(task_data, worker_id=worker_id)
                return task_data

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
    ) -> dict[str, Any] | None:
        """Persist one terminal or retry transition for a processed task."""
        with get_db() as db:
            should_retry = (
                not success and retryable and current_retry_count < max(int(max_retries), 0)
            )
            resolved_delay_seconds = retry_delay_seconds if should_retry else None
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
                    "Task retry scheduled",
                    extra=build_log_extra(
                        component="queue",
                        operation="finalize_task",
                        event_name="task.retry_scheduled",
                        status="retry_scheduled",
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

    def get_queue_stats(self) -> dict[str, Any]:
        """Get queue statistics."""
        with get_db() as db:
            stats = {}

            # Count by status
            status_counts = (
                db.query(ProcessingTask.status, func.count(ProcessingTask.id))
                .group_by(ProcessingTask.status)
                .all()
            )

            stats["by_status"] = {status: count for status, count in status_counts}

            # Count by type
            type_counts = (
                db.query(ProcessingTask.task_type, func.count(ProcessingTask.id))
                .filter(ProcessingTask.status == TaskStatus.PENDING.value)
                .group_by(ProcessingTask.task_type)
                .all()
            )

            stats["pending_by_type"] = {task_type: count for task_type, count in type_counts}

            queue_counts = (
                db.query(ProcessingTask.queue_name, func.count(ProcessingTask.id))
                .filter(ProcessingTask.status == TaskStatus.PENDING.value)
                .group_by(ProcessingTask.queue_name)
                .all()
            )
            stats["pending_by_queue"] = {queue_name: count for queue_name, count in queue_counts}

            queue_type_counts = (
                db.query(
                    ProcessingTask.queue_name,
                    ProcessingTask.task_type,
                    func.count(ProcessingTask.id),
                )
                .filter(ProcessingTask.status == TaskStatus.PENDING.value)
                .group_by(ProcessingTask.queue_name, ProcessingTask.task_type)
                .all()
            )
            pending_by_queue_type: dict[str, dict[str, int]] = {}
            for queue_name, task_type, count in queue_type_counts:
                if queue_name not in pending_by_queue_type:
                    pending_by_queue_type[queue_name] = {}
                pending_by_queue_type[queue_name][task_type] = count
            stats["pending_by_queue_type"] = pending_by_queue_type

            # Failed tasks in last hour
            one_hour_ago = _utc_now() - timedelta(hours=1)
            recent_failures = (
                db.query(func.count(ProcessingTask.id))
                .filter(
                    and_(
                        ProcessingTask.status == TaskStatus.FAILED.value,
                        ProcessingTask.completed_at >= one_hour_ago,
                    )
                )
                .scalar()
            )

            stats["recent_failures"] = recent_failures

            return stats

    def get_backpressure_status(self) -> dict[str, Any]:
        """Return whether pending queue backlog is healthy enough for cron enqueue work."""
        settings = get_settings()
        stats = self.get_queue_stats()
        pending_by_queue = stats.get("pending_by_queue", {})
        pending_by_queue_type = stats.get("pending_by_queue_type", {})
        content_pending = int(pending_by_queue.get(TaskQueue.CONTENT.value, 0))
        content_pending_by_type = pending_by_queue_type.get(TaskQueue.CONTENT.value, {})
        pending_process_news_item = int(
            content_pending_by_type.get(TaskType.PROCESS_NEWS_ITEM.value, 0)
        )
        pending_generate_agent_digest = int(
            content_pending_by_type.get(TaskType.GENERATE_AGENT_DIGEST.value, 0)
        )
        reasons: list[str] = []
        if content_pending >= settings.queue_backpressure_max_pending_content:
            reasons.append("content_queue_backlog")
        if pending_process_news_item >= settings.queue_backpressure_max_pending_process_news_item:
            reasons.append("process_news_item_backlog")
        if (
            pending_generate_agent_digest
            >= settings.queue_backpressure_max_pending_generate_agent_digest
        ):
            reasons.append("generate_agent_digest_backlog")
        return {
            "should_throttle": bool(reasons),
            "reasons": reasons,
            "counts": {
                "pending_content": content_pending,
                "pending_process_news_item": pending_process_news_item,
                "pending_generate_agent_digest": pending_generate_agent_digest,
            },
            "thresholds": {
                "pending_content": settings.queue_backpressure_max_pending_content,
                "pending_process_news_item": (
                    settings.queue_backpressure_max_pending_process_news_item
                ),
                "pending_generate_agent_digest": (
                    settings.queue_backpressure_max_pending_generate_agent_digest
                ),
            },
        }

    def cleanup_old_tasks(self, days: int = 7):
        """Remove completed tasks older than specified days."""
        with get_db() as db:
            cutoff_date = _utc_now() - timedelta(days=days)

            deleted = (
                db.query(ProcessingTask)
                .filter(
                    and_(
                        ProcessingTask.status == TaskStatus.COMPLETED.value,
                        ProcessingTask.completed_at < cutoff_date,
                    )
                )
                .delete()
            )

            logger.info(f"Cleaned up {deleted} old completed tasks")


# Global instance
_queue_service = None


def get_queue_service() -> QueueService:
    """Get the global queue service instance."""
    global _queue_service
    if _queue_service is None:
        _queue_service = QueueService()
    return _queue_service
