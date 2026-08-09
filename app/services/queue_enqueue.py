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
from app.models.db import ProcessingTask, ProcessingTaskUserAccess, User
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
    owner_user_id: int | None = None
    access_user_id: int | None = None
    available_at: datetime | None = None


@dataclass(frozen=True)
class _PreparedEnqueue:
    """Validated queue row plus its resolved active-task identity."""

    request: TaskEnqueueRequest
    queue_name: str
    payload: dict[str, Any] | None
    dedupe_key: str | None
    owner_user_id: int | None
    access_user_id: int | None
    available_at: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize_payload_for_dedupe(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _upsert_task_access_grants(
    db: Session,
    grants: set[tuple[int, int]],
) -> None:
    """Insert task-access grants idempotently in the caller transaction."""
    if not grants:
        return
    created_at = _utc_now()
    db.execute(
        postgresql_insert(ProcessingTaskUserAccess)
        .values(
            [
                {"task_id": task_id, "user_id": user_id, "created_at": created_at}
                for task_id, user_id in sorted(grants)
            ]
        )
        .on_conflict_do_nothing(
            index_elements=[
                ProcessingTaskUserAccess.task_id,
                ProcessingTaskUserAccess.user_id,
            ]
        )
    )


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
        owner_user_id: int | None = None,
        access_user_id: int | None = None,
        available_at: datetime | None = None,
    ) -> int:
        """Add one task to the queue and return its id."""
        with self._queue_db() as db:
            return self.enqueue_many_in_session(
                db,
                [
                    TaskEnqueueRequest(
                        task_type=task_type,
                        content_id=content_id,
                        payload=payload,
                        queue_name=queue_name,
                        dedupe=dedupe,
                        dedupe_key=dedupe_key,
                        owner_user_id=owner_user_id,
                        access_user_id=access_user_id,
                        available_at=available_at,
                    )
                ],
            )[0]

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
        self._lock_active_users(db, prepared)
        task_ids, inserted_task_ids = self._insert_prepared_batch(db, prepared)
        self._validate_resolved_owners(db, prepared, task_ids)
        self._grant_task_access(db, prepared, task_ids)
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

    def grant_access_in_session(self, db: Session, *, task_id: int, user_id: int) -> None:
        """Grant one active user polling access to an already-resolved task."""

        normalized_user_id = _positive_user_id(user_id, field="user_id")
        if normalized_user_id is None:
            raise ValueError("user_id is required")
        self._lock_active_user_ids(db, [normalized_user_id])
        if db.query(ProcessingTask.id).filter(ProcessingTask.id == task_id).first() is None:
            raise ValueError("Task does not exist")
        _upsert_task_access_grants(db, {(task_id, normalized_user_id)})

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
        owner_user_id = _positive_user_id(request.owner_user_id, field="owner_user_id")
        access_user_id = _positive_user_id(request.access_user_id, field="access_user_id")
        if task_spec.requires_owner and owner_user_id is None:
            raise ValueError(f"Task {request.task_type.value} requires owner_user_id")
        payload_user_id = _positive_user_id(task_payload.get("user_id"), field="payload.user_id")
        if task_spec.requires_owner and payload_user_id != owner_user_id:
            raise ValueError("Task owner_user_id must match payload user_id")
        if owner_user_id is not None and access_user_id is None:
            access_user_id = owner_user_id
        return _PreparedEnqueue(
            request,
            target_queue,
            task_payload,
            resolved_dedupe_key,
            owner_user_id,
            access_user_id,
            request.available_at or _utc_now(),
        )

    @staticmethod
    def _lock_active_users(db: Session, prepared: list[_PreparedEnqueue]) -> None:
        user_ids = sorted(
            {
                user_id
                for row in prepared
                for user_id in (row.owner_user_id, row.access_user_id)
                if user_id is not None
            }
        )
        QueueEnqueueMixin._lock_active_user_ids(db, user_ids)

    @staticmethod
    def _lock_active_user_ids(db: Session, user_ids: list[int]) -> None:
        if not user_ids:
            return
        active_ids = {
            int(user_id)
            for (user_id,) in (
                db.query(User.id)
                .filter(User.id.in_(user_ids), User.is_active.is_(True))
                .with_for_update(read=True)
                .all()
            )
        }
        missing_ids = set(user_ids).difference(active_ids)
        if missing_ids:
            raise ValueError("Task user is missing or inactive")

    @staticmethod
    def _validate_resolved_owners(
        db: Session,
        prepared: list[_PreparedEnqueue],
        task_ids: list[int],
    ) -> None:
        owned_tasks = [
            (row, task_id)
            for row, task_id in zip(prepared, task_ids, strict=True)
            if row.owner_user_id is not None
        ]
        if not owned_tasks:
            return
        owner_by_task_id = {
            int(task_id): int(owner_user_id) if owner_user_id is not None else None
            for task_id, owner_user_id in db.query(
                ProcessingTask.id,
                ProcessingTask.owner_user_id,
            )
            .filter(ProcessingTask.id.in_([task_id for _row, task_id in owned_tasks]))
            .all()
        }
        for row, task_id in owned_tasks:
            if owner_by_task_id.get(task_id) != row.owner_user_id:
                raise ValueError("Deduplicated task belongs to another user")

    @staticmethod
    def _grant_task_access(
        db: Session,
        prepared: list[_PreparedEnqueue],
        task_ids: list[int],
    ) -> None:
        grants = {
            (task_id, row.access_user_id)
            for row, task_id in zip(prepared, task_ids, strict=True)
            if row.access_user_id is not None
        }
        _upsert_task_access_grants(db, grants)

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
                        "available_at": row.available_at,
                        "dedupe_key": dedupe_key,
                        "owner_user_id": row.owner_user_id,
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
                available_at=row.available_at,
                dedupe_key=None,
                owner_user_id=row.owner_user_id,
            )
            db.add(task)
            rows.append((index, task))
        if rows:
            db.flush()
        return rows

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


def _positive_user_id(value: int | None, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return int(value)
