"""Task enqueue and active-task deduplication operations."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.db import ProcessingTask
from app.pipeline.task_specs import get_task_spec

logger = get_logger(__name__)

ACTIVE_TASK_STATUSES: tuple[str, str] = (
    TaskStatus.PENDING.value,
    TaskStatus.PROCESSING.value,
)
ACTIVE_DEDUPE_INDEX_WHERE = text("dedupe_key IS NOT NULL AND status IN ('pending', 'processing')")


@dataclass(frozen=True)
class TaskEnqueueRequest:
    """One task request accepted by a queue batch."""

    task_type: TaskType
    content_id: int | None = None
    payload: dict[str, Any] | None = None
    queue_name: TaskQueue | str | None = None
    dedupe: bool | None = None
    dedupe_key: str | None = None


@dataclass(frozen=True)
class _PreparedEnqueue:
    """Validated queue row plus its resolved active-task identity."""

    request: TaskEnqueueRequest
    queue_name: str
    payload: dict[str, Any] | None
    dedupe_key: str | None


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize_payload_for_dedupe(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def build_task_dedupe_key(
    *,
    task_type: TaskType,
    content_id: int | None,
    queue_name: TaskQueue | str,
    payload: dict[str, Any] | None = None,
    should_dedupe: bool = True,
) -> str | None:
    """Build a stable dedupe key for active work items."""
    if not should_dedupe:
        return None

    queue_value = queue_name.value if isinstance(queue_name, TaskQueue) else queue_name
    parts = [queue_value, task_type.value]
    if content_id is not None:
        parts.append(f"content:{content_id}")
    payload_fragment = _normalize_payload_for_dedupe(payload)
    if payload_fragment is not None and content_id is None:
        parts.append(f"payload:{payload_fragment}")
    return "|".join(parts)


def _lookup_active_task_id_by_dedupe_key(db: Session, *, dedupe_key: str) -> int | None:
    task_id = (
        db.query(ProcessingTask.id)
        .filter(ProcessingTask.dedupe_key == dedupe_key)
        .filter(ProcessingTask.status.in_(ACTIVE_TASK_STATUSES))
        .scalar()
    )
    return int(task_id) if task_id is not None else None


class QueueEnqueueMixin:
    """Enqueue behavior for a host that provides ``_queue_db()``."""

    def _queue_db(self) -> AbstractContextManager[Session]:
        raise NotImplementedError

    @staticmethod
    def _normalize_queue_name(queue_name: TaskQueue | str | None) -> str | None:
        if queue_name is None:
            return None
        if isinstance(queue_name, TaskQueue):
            return queue_name.value
        return TaskQueue(queue_name).value

    def enqueue(
        self,
        task_type: TaskType,
        content_id: int | None = None,
        payload: dict[str, Any] | None = None,
        queue_name: TaskQueue | str | None = None,
        dedupe: bool | None = None,
        dedupe_key: str | None = None,
    ) -> int:
        """Add one task to the queue and return its id."""
        task_spec = get_task_spec(task_type)
        target_queue = self._normalize_queue_name(queue_name) or task_spec.queue.value
        task_payload = task_spec.normalize_payload(payload)
        with self._queue_db() as db:
            should_dedupe = dedupe if dedupe is not None else task_spec.dedupe_by_content
            resolved_dedupe_key = dedupe_key
            if resolved_dedupe_key is None:
                resolved_dedupe_key = build_task_dedupe_key(
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
                    self._notify_one(db, task_id, task_type, target_queue)
                    self._log_enqueue(task_id, task_type, target_queue, content_id, payload)
                    return task_id

                existing_task_id = _lookup_active_task_id_by_dedupe_key(
                    db,
                    dedupe_key=resolved_dedupe_key,
                )
                if existing_task_id is not None:
                    self._log_reuse(
                        existing_task_id,
                        task_type,
                        target_queue,
                        content_id,
                    )
                    return existing_task_id
                raise RuntimeError(
                    "Task dedupe conflict did not return an inserted task or an active task"
                )

            task = ProcessingTask(
                task_type=task_type.value,
                content_id=content_id,
                payload=task_payload,
                status=TaskStatus.PENDING.value,
                queue_name=target_queue,
                available_at=_utc_now(),
                dedupe_key=None,
            )
            db.add(task)
            db.flush()
            if task.id is None:
                raise ValueError("Processing task insert did not produce an id")
            task_id = int(task.id)
            self._notify_one(db, task_id, task_type, target_queue)
            self._log_enqueue(task_id, task_type, target_queue, content_id, payload)
            return task_id

    def enqueue_many(self, requests: list[TaskEnqueueRequest]) -> list[int]:
        """Add a batch in one transaction and wake workers once."""
        if not requests:
            return []
        with self._queue_db() as db:
            return self.enqueue_many_in_session(db, requests)

    def enqueue_many_in_session(
        self,
        db: Session,
        requests: list[TaskEnqueueRequest],
    ) -> list[int]:
        """Add a task batch to an existing caller-owned transaction."""
        if not requests:
            return []

        prepared = [self._prepare_enqueue_request(request) for request in requests]
        task_ids, inserted_task_ids = self._insert_prepared_batch(db, prepared)
        if inserted_task_ids:
            notification_payload = json.dumps(
                {"task_ids": inserted_task_ids[:100], "count": len(inserted_task_ids)},
                separators=(",", ":"),
            )
            db.execute(select(func.pg_notify("processing_tasks", notification_payload)))

        unlogged_inserted_ids = set(inserted_task_ids)
        for request, prepared_request, task_id in zip(requests, prepared, task_ids, strict=True):
            if task_id in unlogged_inserted_ids:
                unlogged_inserted_ids.discard(task_id)
                self._log_enqueue(
                    task_id,
                    request.task_type,
                    prepared_request.queue_name,
                    request.content_id,
                    request.payload,
                )
            else:
                self._log_reuse(
                    task_id,
                    request.task_type,
                    prepared_request.queue_name,
                    request.content_id,
                )
        return task_ids

    def _prepare_enqueue_request(self, request: TaskEnqueueRequest) -> _PreparedEnqueue:
        task_spec = get_task_spec(request.task_type)
        target_queue = self._normalize_queue_name(request.queue_name) or task_spec.queue.value
        task_payload = task_spec.normalize_payload(request.payload)
        should_dedupe = (
            request.dedupe if request.dedupe is not None else task_spec.dedupe_by_content
        )
        resolved_dedupe_key = request.dedupe_key
        if resolved_dedupe_key is None:
            resolved_dedupe_key = build_task_dedupe_key(
                task_type=request.task_type,
                content_id=request.content_id,
                payload=task_payload,
                queue_name=target_queue,
                should_dedupe=should_dedupe,
            )
        return _PreparedEnqueue(request, target_queue, task_payload, resolved_dedupe_key)

    def _insert_prepared_batch(
        self,
        db: Session,
        prepared: list[_PreparedEnqueue],
    ) -> tuple[list[int], list[int]]:
        dedupe_keys = list(
            dict.fromkeys(row.dedupe_key for row in prepared if row.dedupe_key is not None)
        )
        task_id_by_dedupe_key = self._active_task_ids_by_dedupe_key(db, dedupe_keys)
        missing_by_key: dict[str, _PreparedEnqueue] = {}
        for row in prepared:
            if row.dedupe_key is not None and row.dedupe_key not in task_id_by_dedupe_key:
                missing_by_key.setdefault(row.dedupe_key, row)

        inserted_task_ids = self._insert_missing_deduped_rows(
            db,
            missing_by_key,
            task_id_by_dedupe_key,
        )
        non_deduped_rows = self._insert_non_deduped_rows(db, prepared)

        task_ids: list[int | None] = [None] * len(prepared)
        for index, row in enumerate(prepared):
            if row.dedupe_key is not None:
                task_ids[index] = task_id_by_dedupe_key[row.dedupe_key]
        for index, task in non_deduped_rows:
            if task.id is None:
                raise ValueError("Processing task insert did not produce an id")
            task_id = int(task.id)
            task_ids[index] = task_id
            inserted_task_ids.append(task_id)

        if any(task_id is None for task_id in task_ids):
            raise RuntimeError("Queue batch did not resolve every task id")
        return [int(task_id) for task_id in task_ids if task_id is not None], inserted_task_ids

    @staticmethod
    def _active_task_ids_by_dedupe_key(db: Session, dedupe_keys: list[str]) -> dict[str, int]:
        if not dedupe_keys:
            return {}
        existing_tasks = (
            db.query(ProcessingTask.dedupe_key, ProcessingTask.id)
            .filter(ProcessingTask.dedupe_key.in_(dedupe_keys))
            .filter(ProcessingTask.status.in_(ACTIVE_TASK_STATUSES))
            .all()
        )
        return {
            str(dedupe_key): int(task_id)
            for dedupe_key, task_id in existing_tasks
            if dedupe_key is not None and task_id is not None
        }

    @staticmethod
    def _insert_missing_deduped_rows(
        db: Session,
        missing_by_key: dict[str, _PreparedEnqueue],
        task_id_by_dedupe_key: dict[str, int],
    ) -> list[int]:
        if not missing_by_key:
            return []
        now = _utc_now()
        inserted_rows = db.execute(
            postgresql_insert(ProcessingTask)
            .values(
                [
                    {
                        "task_type": row.request.task_type.value,
                        "content_id": row.request.content_id,
                        "payload": row.payload,
                        "status": TaskStatus.PENDING.value,
                        "queue_name": row.queue_name,
                        "available_at": now,
                        "dedupe_key": dedupe_key,
                    }
                    for dedupe_key, row in missing_by_key.items()
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[ProcessingTask.dedupe_key],
                index_where=ACTIVE_DEDUPE_INDEX_WHERE,
            )
            .returning(ProcessingTask.id, ProcessingTask.dedupe_key)
        ).all()
        inserted_task_ids: list[int] = []
        for task_id, dedupe_key in inserted_rows:
            normalized_task_id = int(task_id)
            task_id_by_dedupe_key[str(dedupe_key)] = normalized_task_id
            inserted_task_ids.append(normalized_task_id)

        unresolved_keys = set(missing_by_key).difference(task_id_by_dedupe_key)
        if unresolved_keys:
            raced_tasks = (
                db.query(ProcessingTask.dedupe_key, ProcessingTask.id)
                .filter(ProcessingTask.dedupe_key.in_(unresolved_keys))
                .filter(ProcessingTask.status.in_(ACTIVE_TASK_STATUSES))
                .all()
            )
            for dedupe_key, task_id in raced_tasks:
                if dedupe_key is not None and task_id is not None:
                    task_id_by_dedupe_key[str(dedupe_key)] = int(task_id)
            unresolved_keys.difference_update(task_id_by_dedupe_key)
            if unresolved_keys:
                raise RuntimeError("Task dedupe conflict did not resolve active task ids")
        return inserted_task_ids

    @staticmethod
    def _insert_non_deduped_rows(
        db: Session,
        prepared: list[_PreparedEnqueue],
    ) -> list[tuple[int, ProcessingTask]]:
        now = _utc_now()
        rows: list[tuple[int, ProcessingTask]] = []
        for index, row in enumerate(prepared):
            if row.dedupe_key is not None:
                continue
            task = ProcessingTask(
                task_type=row.request.task_type.value,
                content_id=row.request.content_id,
                payload=row.payload,
                status=TaskStatus.PENDING.value,
                queue_name=row.queue_name,
                available_at=now,
                dedupe_key=None,
            )
            db.add(task)
            rows.append((index, task))
        if rows:
            db.flush()
        return rows

    @staticmethod
    def _notify_one(db: Session, task_id: int, task_type: TaskType, queue_name: str) -> None:
        notification_payload = json.dumps(
            {"task_id": task_id, "task_type": task_type.value, "queue_name": queue_name},
            separators=(",", ":"),
        )
        db.execute(select(func.pg_notify("processing_tasks", notification_payload)))

    @staticmethod
    def _log_enqueue(
        task_id: int,
        task_type: TaskType,
        queue_name: str,
        content_id: int | None,
        payload: dict[str, Any] | None,
    ) -> None:
        logger.info(
            "Task enqueued",
            extra=build_log_extra(
                component="queue",
                operation="enqueue",
                event_name="task.enqueued",
                status="completed",
                task_id=task_id,
                task_type=task_type.value,
                queue_name=queue_name,
                content_id=content_id,
                context_data={"has_payload": bool(payload)},
            ),
        )

    @staticmethod
    def _log_reuse(
        task_id: int,
        task_type: TaskType,
        queue_name: str,
        content_id: int | None,
    ) -> None:
        logger.info(
            "Reusing existing task",
            extra=build_log_extra(
                component="queue",
                operation="enqueue",
                event_name="task.reused",
                status="completed",
                task_id=task_id,
                task_type=task_type.value,
                queue_name=queue_name,
                content_id=content_id,
            ),
        )
