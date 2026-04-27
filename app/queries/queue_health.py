"""Queue health read model for admin/operator surfaces."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.models.contracts import TaskStatus
from app.models.schema import ProcessingTask


class QueueTaskBacklog(BaseModel):
    queue_name: str
    task_type: str
    pending_count: int
    oldest_pending_age_seconds: float | None


class QueueRetryBucket(BaseModel):
    retry_count: int
    pending_count: int


class QueueFailureSummary(BaseModel):
    task_type: str
    error_message: str
    count: int


class QueueHealthSnapshot(BaseModel):
    generated_at: datetime
    window_hours: int = Field(ge=1)
    pending: list[QueueTaskBacklog]
    processing_count: int
    expired_lease_count: int
    retry_buckets: list[QueueRetryBucket]
    recent_failed_count: int
    top_failures: list[QueueFailureSummary]


def get_queue_health_snapshot(
    db: Session,
    *,
    window_hours: int = 24,
    top_errors_limit: int = 10,
) -> QueueHealthSnapshot:
    """Build a bounded queue SLO snapshot from `processing_tasks`."""
    now = _utc_now()
    cutoff = now - timedelta(hours=window_hours)

    pending = _pending_backlog(db, now=now)
    processing_count = int(
        db.query(func.count(ProcessingTask.id))
        .filter(ProcessingTask.status == TaskStatus.PROCESSING.value)
        .scalar()
        or 0
    )
    expired_lease_count = int(
        db.query(func.count(ProcessingTask.id))
        .filter(ProcessingTask.status == TaskStatus.PROCESSING.value)
        .filter(ProcessingTask.lease_expires_at.is_not(None))
        .filter(ProcessingTask.lease_expires_at <= now)
        .scalar()
        or 0
    )
    retry_buckets = _retry_buckets(db)
    recent_failed_count = int(
        db.query(func.count(ProcessingTask.id))
        .filter(ProcessingTask.status == TaskStatus.FAILED.value)
        .filter(_task_recent_filter(cutoff))
        .scalar()
        or 0
    )
    top_failures = _top_failures(db, cutoff=cutoff, limit=top_errors_limit)

    return QueueHealthSnapshot(
        generated_at=now.replace(tzinfo=UTC),
        window_hours=window_hours,
        pending=pending,
        processing_count=processing_count,
        expired_lease_count=expired_lease_count,
        retry_buckets=retry_buckets,
        recent_failed_count=recent_failed_count,
        top_failures=top_failures,
    )


def _pending_backlog(db: Session, *, now: datetime) -> list[QueueTaskBacklog]:
    oldest_at = func.min(func.coalesce(ProcessingTask.available_at, ProcessingTask.created_at))
    rows = (
        db.query(
            ProcessingTask.queue_name,
            ProcessingTask.task_type,
            func.count(ProcessingTask.id),
            oldest_at,
        )
        .filter(ProcessingTask.status == TaskStatus.PENDING.value)
        .group_by(ProcessingTask.queue_name, ProcessingTask.task_type)
        .order_by(ProcessingTask.queue_name.asc(), ProcessingTask.task_type.asc())
        .all()
    )
    return [
        QueueTaskBacklog(
            queue_name=str(queue_name or "unknown"),
            task_type=str(task_type or "unknown"),
            pending_count=int(count or 0),
            oldest_pending_age_seconds=_age_seconds(now, oldest_pending_at),
        )
        for queue_name, task_type, count, oldest_pending_at in rows
    ]


def _retry_buckets(db: Session) -> list[QueueRetryBucket]:
    retry_count = func.coalesce(ProcessingTask.retry_count, 0)
    rows = (
        db.query(retry_count, func.count(ProcessingTask.id))
        .filter(ProcessingTask.status == TaskStatus.PENDING.value)
        .group_by(retry_count)
        .order_by(retry_count.asc())
        .all()
    )
    return [
        QueueRetryBucket(retry_count=int(retry_count or 0), pending_count=int(count or 0))
        for retry_count, count in rows
    ]


def _top_failures(db: Session, *, cutoff: datetime, limit: int) -> list[QueueFailureSummary]:
    error_message = func.coalesce(ProcessingTask.error_message, "unknown")
    rows = (
        db.query(
            ProcessingTask.task_type,
            error_message,
            func.count(ProcessingTask.id).label("failure_count"),
        )
        .filter(ProcessingTask.status == TaskStatus.FAILED.value)
        .filter(_task_recent_filter(cutoff))
        .group_by(ProcessingTask.task_type, error_message)
        .order_by(func.count(ProcessingTask.id).desc(), ProcessingTask.task_type.asc())
        .limit(max(limit, 1))
        .all()
    )
    return [
        QueueFailureSummary(
            task_type=str(task_type or "unknown"),
            error_message=str(error_message or "unknown"),
            count=int(count or 0),
        )
        for task_type, error_message, count in rows
    ]


def _task_recent_filter(cutoff: datetime):
    return or_(
        ProcessingTask.completed_at >= cutoff,
        and_(ProcessingTask.completed_at.is_(None), ProcessingTask.created_at >= cutoff),
    )


def _age_seconds(now: datetime, then: datetime | None) -> float | None:
    if then is None:
        return None
    return max((now - _as_naive_utc(then)).total_seconds(), 0.0)


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
