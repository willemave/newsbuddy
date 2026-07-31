"""Atomic SQLAlchemy Core operations for processing-task lease ownership."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import Engine

from app.core.db import get_engine
from app.models.contracts import TaskStatus
from app.models.db import ProcessingTask
from app.models.db.tasks import processing_task_lease_clear_values
from app.models.internal.queue import ClaimedTask, TaskTransition

_TASKS = ProcessingTask.__table__

_CLAIM_COLUMNS = (
    _TASKS.c.id,
    _TASKS.c.task_type,
    _TASKS.c.content_id,
    _TASKS.c.payload,
    _TASKS.c.retry_count,
    _TASKS.c.status,
    _TASKS.c.queue_name,
    _TASKS.c.created_at,
    _TASKS.c.available_at,
    _TASKS.c.started_at,
    _TASKS.c.locked_at,
    _TASKS.c.locked_by,
    _TASKS.c.lease_token,
    _TASKS.c.lease_expires_at,
)

_TRANSITION_COLUMNS = (
    _TASKS.c.task_type,
    _TASKS.c.queue_name,
    _TASKS.c.content_id,
    _TASKS.c.error_message,
    _TASKS.c.status,
    _TASKS.c.retry_count,
    _TASKS.c.available_at,
)


class FinalizationOutcome(StrEnum):
    """Database transition selected after one processing attempt."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY = "retry"
    DEFERRED = "deferred"


def _database_now():
    """Return PostgreSQL's transaction timestamp normalized to naive UTC."""
    return func.timezone("UTC", func.now())


def _claimable_filters():
    now = _database_now()
    return or_(
        and_(
            _TASKS.c.status == TaskStatus.PENDING.value,
            _TASKS.c.available_at <= now,
        ),
        and_(
            _TASKS.c.status == TaskStatus.PROCESSING.value,
            _TASKS.c.lease_expires_at.is_not(None),
            _TASKS.c.lease_expires_at <= now,
        ),
    )


class ProcessingTaskQueueRepository:
    """Own claim, heartbeat, and finalization transactions for the DB queue."""

    def __init__(self, engine: Engine | None = None) -> None:
        self._configured_engine = engine

    @property
    def _engine(self) -> Engine:
        return self._configured_engine or get_engine()

    def list_claimable_retry_counts(
        self,
        *,
        task_type: str | None,
        queue_name: str | None,
    ) -> list[int]:
        """Return retry buckets that currently contain claimable tasks."""
        filters = [_claimable_filters()]
        if task_type is not None:
            filters.append(_TASKS.c.task_type == task_type)
        if queue_name is not None:
            filters.append(_TASKS.c.queue_name == queue_name)

        statement = (
            select(_TASKS.c.retry_count).where(*filters).distinct().order_by(_TASKS.c.retry_count)
        )
        with self._engine.connect() as connection:
            return [int(value) for value in connection.execute(statement).scalars()]

    def claim_task(
        self,
        *,
        lease_seconds: int,
        worker_id: str,
        retry_count: int,
        task_type: str | None,
        queue_name: str | None,
    ) -> ClaimedTask | None:
        """Atomically claim one ready row and return its validated task shape."""
        filters = [_claimable_filters(), _TASKS.c.retry_count == retry_count]
        if task_type is not None:
            filters.append(_TASKS.c.task_type == task_type)
        if queue_name is not None:
            filters.append(_TASKS.c.queue_name == queue_name)

        candidate_id = (
            select(_TASKS.c.id)
            .where(*filters)
            .order_by(
                _TASKS.c.available_at.asc(),
                _TASKS.c.created_at.asc(),
                _TASKS.c.id.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        lease_token = uuid4()
        now = _database_now()
        statement = (
            update(_TASKS)
            .where(_TASKS.c.id == candidate_id)
            .values(
                status=TaskStatus.PROCESSING.value,
                started_at=now,
                locked_at=now,
                locked_by=worker_id,
                lease_token=lease_token,
                lease_expires_at=now + timedelta(seconds=max(lease_seconds, 1)),
            )
            .returning(*_CLAIM_COLUMNS)
        )

        with self._engine.begin() as connection:
            row = connection.execute(statement).mappings().first()
            if row is None:
                return None
            claimed = ClaimedTask.model_validate(dict(row))
            if claimed.locked_by != worker_id or claimed.lease_token != lease_token:
                raise RuntimeError("Claimed task ownership does not match the claim request")
            if queue_name is not None and claimed.queue_name != queue_name:
                raise RuntimeError("Claimed task queue does not match the requested queue")
            return claimed

    def renew_lease(
        self,
        claim: ClaimedTask,
        *,
        lease_seconds: int,
    ) -> bool:
        """Extend an unexpired lease only for its exact claim owner."""
        now = _database_now()
        statement = (
            update(_TASKS)
            .where(
                _TASKS.c.id == claim.id,
                _TASKS.c.status == TaskStatus.PROCESSING.value,
                _TASKS.c.locked_by == claim.locked_by,
                _TASKS.c.lease_token == claim.lease_token,
                _TASKS.c.lease_expires_at > now,
                _TASKS.c.retry_count == claim.retry_count,
            )
            .values(
                locked_at=now,
                lease_expires_at=now + timedelta(seconds=max(lease_seconds, 1)),
            )
            .returning(_TASKS.c.id)
        )
        with self._engine.begin() as connection:
            return connection.execute(statement).scalar_one_or_none() is not None

    def finalize_task(
        self,
        claim: ClaimedTask,
        *,
        outcome: FinalizationOutcome,
        error_message: str | None = None,
        retry_delay_seconds: int | None = None,
    ) -> TaskTransition | None:
        """Apply one terminal, retry, or deferral transition if ownership is current."""
        values = self._finalization_values(
            outcome=outcome,
            error_message=error_message,
            retry_delay_seconds=retry_delay_seconds,
        )
        statement = (
            # PostgreSQL evaluates this once per transaction, including after
            # connection-pool waits, so an already-expired claim cannot pass.
            update(_TASKS)
            .where(
                _TASKS.c.id == claim.id,
                _TASKS.c.status == TaskStatus.PROCESSING.value,
                _TASKS.c.locked_by == claim.locked_by,
                _TASKS.c.lease_token == claim.lease_token,
                _TASKS.c.lease_expires_at > _database_now(),
                _TASKS.c.retry_count == claim.retry_count,
            )
            .values(**values)
            .returning(*_TRANSITION_COLUMNS)
        )
        with self._engine.begin() as connection:
            row = connection.execute(statement).mappings().first()
            if row is None:
                return None
            return TaskTransition.model_validate(
                {
                    **dict(row),
                    "retry_delay_seconds": retry_delay_seconds
                    if outcome
                    in {
                        FinalizationOutcome.RETRY,
                        FinalizationOutcome.DEFERRED,
                    }
                    else None,
                    "deferred": outcome is FinalizationOutcome.DEFERRED,
                }
            )

    @staticmethod
    def _finalization_values(
        *,
        outcome: FinalizationOutcome,
        error_message: str | None,
        retry_delay_seconds: int | None,
    ) -> dict[str, object]:
        """Build the single update payload for a finalization outcome."""
        now = _database_now()
        values: dict[str, object] = processing_task_lease_clear_values()
        if outcome is FinalizationOutcome.SUCCEEDED:
            return {
                **values,
                "status": TaskStatus.COMPLETED.value,
                "completed_at": now,
                "error_message": None,
            }
        if outcome is FinalizationOutcome.FAILED:
            return {
                **values,
                "status": TaskStatus.FAILED.value,
                "completed_at": now,
                "error_message": error_message,
            }

        if retry_delay_seconds is None:
            raise ValueError(f"{outcome.value} finalization requires a resolved retry delay")
        values.update(
            status=TaskStatus.PENDING.value,
            started_at=None,
            completed_at=None,
            available_at=now + timedelta(seconds=retry_delay_seconds),
            error_message=(None if outcome is FinalizationOutcome.DEFERRED else error_message),
        )
        if outcome is FinalizationOutcome.RETRY:
            values["retry_count"] = _TASKS.c.retry_count + 1
        return values
