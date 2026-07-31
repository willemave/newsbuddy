"""Queue health read model for admin/operator surfaces."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from app.models.contracts import TaskStatus
from app.models.db import ProcessingTask


class QueueTaskBacklog(BaseModel):
    queue_name: str
    task_type: str
    pending_count: int
    oldest_pending_age_seconds: float | None


class QueueRetryBucket(BaseModel):
    retry_count: int
    pending_count: int


class QueueProcessingBacklog(BaseModel):
    queue_name: str
    task_type: str
    processing_count: int
    oldest_processing_age_seconds: float | None


class QueueTaskActivity(BaseModel):
    queue_name: str
    task_type: str
    enqueued_count: int
    completed_count: int
    failed_count: int


class QueueTaskLatency(BaseModel):
    queue_name: str
    task_type: str
    sample_count: int
    ready_wait_p50_seconds: float
    ready_wait_p95_seconds: float
    total_wait_p50_seconds: float
    total_wait_p95_seconds: float
    run_time_p50_seconds: float
    run_time_p95_seconds: float


class QueueFailureSummary(BaseModel):
    task_type: str
    error_message: str
    count: int


class QueueHealthSnapshot(BaseModel):
    generated_at: datetime
    window_hours: int = Field(ge=1)
    pending: list[QueueTaskBacklog]
    processing: list[QueueProcessingBacklog]
    activity: list[QueueTaskActivity]
    latency: list[QueueTaskLatency]
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
    processing = _processing_backlog(db, now=now)
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
    activity = _task_activity(db, cutoff=cutoff)
    latency = _task_latency(db, cutoff=cutoff)
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
        processing=processing,
        activity=activity,
        latency=latency,
        processing_count=processing_count,
        expired_lease_count=expired_lease_count,
        retry_buckets=retry_buckets,
        recent_failed_count=recent_failed_count,
        top_failures=top_failures,
    )


def _processing_backlog(db: Session, *, now: datetime) -> list[QueueProcessingBacklog]:
    oldest_at = func.min(
        func.coalesce(
            ProcessingTask.started_at,
            ProcessingTask.locked_at,
            ProcessingTask.created_at,
        )
    )
    rows = (
        db.query(
            ProcessingTask.queue_name,
            ProcessingTask.task_type,
            func.count(ProcessingTask.id),
            oldest_at,
        )
        .filter(ProcessingTask.status == TaskStatus.PROCESSING.value)
        .group_by(ProcessingTask.queue_name, ProcessingTask.task_type)
        .order_by(
            oldest_at.asc(),
            func.count(ProcessingTask.id).desc(),
            ProcessingTask.queue_name.asc(),
            ProcessingTask.task_type.asc(),
        )
        .all()
    )
    return [
        QueueProcessingBacklog(
            queue_name=str(queue_name or "unknown"),
            task_type=str(task_type or "unknown"),
            processing_count=int(count or 0),
            oldest_processing_age_seconds=_age_seconds(now, oldest_processing_at),
        )
        for queue_name, task_type, count, oldest_processing_at in rows
    ]


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
        .order_by(
            oldest_at.asc(),
            func.count(ProcessingTask.id).desc(),
            ProcessingTask.queue_name.asc(),
            ProcessingTask.task_type.asc(),
        )
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


def _task_activity(db: Session, *, cutoff: datetime) -> list[QueueTaskActivity]:
    enqueued_count = func.sum(case((ProcessingTask.created_at >= cutoff, 1), else_=0))
    completed_count = func.sum(
        case(
            (
                and_(
                    ProcessingTask.status == TaskStatus.COMPLETED.value,
                    ProcessingTask.completed_at >= cutoff,
                ),
                1,
            ),
            else_=0,
        )
    )
    failed_count = func.sum(
        case(
            (
                and_(
                    ProcessingTask.status == TaskStatus.FAILED.value,
                    ProcessingTask.completed_at >= cutoff,
                ),
                1,
            ),
            else_=0,
        )
    )
    rows = (
        db.query(
            ProcessingTask.queue_name,
            ProcessingTask.task_type,
            enqueued_count,
            completed_count,
            failed_count,
        )
        .filter(
            or_(
                ProcessingTask.created_at >= cutoff,
                ProcessingTask.completed_at >= cutoff,
            )
        )
        .group_by(ProcessingTask.queue_name, ProcessingTask.task_type)
        .order_by(
            enqueued_count.desc(),
            failed_count.desc(),
            ProcessingTask.queue_name.asc(),
            ProcessingTask.task_type.asc(),
        )
        .all()
    )
    return [
        QueueTaskActivity(
            queue_name=str(queue_name or "unknown"),
            task_type=str(task_type or "unknown"),
            enqueued_count=int(enqueued or 0),
            completed_count=int(completed or 0),
            failed_count=int(failed or 0),
        )
        for queue_name, task_type, enqueued, completed, failed in rows
    ]


def _task_latency(db: Session, *, cutoff: datetime) -> list[QueueTaskLatency]:
    ready_wait_seconds = func.greatest(
        func.extract("epoch", ProcessingTask.started_at - ProcessingTask.available_at),
        0.0,
    )
    total_wait_seconds = func.greatest(
        func.extract("epoch", ProcessingTask.started_at - ProcessingTask.created_at),
        0.0,
    )
    run_time_seconds = func.greatest(
        func.extract("epoch", ProcessingTask.completed_at - ProcessingTask.started_at),
        0.0,
    )
    sample_count = func.count(ProcessingTask.id)
    rows = (
        db.query(
            ProcessingTask.queue_name,
            ProcessingTask.task_type,
            sample_count,
            func.percentile_cont(0.5).within_group(ready_wait_seconds),
            func.percentile_cont(0.95).within_group(ready_wait_seconds),
            func.percentile_cont(0.5).within_group(total_wait_seconds),
            func.percentile_cont(0.95).within_group(total_wait_seconds),
            func.percentile_cont(0.5).within_group(run_time_seconds),
            func.percentile_cont(0.95).within_group(run_time_seconds),
        )
        .filter(ProcessingTask.status.in_((TaskStatus.COMPLETED.value, TaskStatus.FAILED.value)))
        .filter(ProcessingTask.completed_at >= cutoff)
        .filter(ProcessingTask.created_at.is_not(None))
        .filter(ProcessingTask.available_at.is_not(None))
        .filter(ProcessingTask.started_at.is_not(None))
        .group_by(ProcessingTask.queue_name, ProcessingTask.task_type)
        .order_by(
            sample_count.desc(),
            ProcessingTask.queue_name.asc(),
            ProcessingTask.task_type.asc(),
        )
        .all()
    )
    return [
        QueueTaskLatency(
            queue_name=str(queue_name or "unknown"),
            task_type=str(task_type or "unknown"),
            sample_count=int(count or 0),
            ready_wait_p50_seconds=float(ready_wait_p50 or 0.0),
            ready_wait_p95_seconds=float(ready_wait_p95 or 0.0),
            total_wait_p50_seconds=float(total_wait_p50 or 0.0),
            total_wait_p95_seconds=float(total_wait_p95 or 0.0),
            run_time_p50_seconds=float(run_time_p50 or 0.0),
            run_time_p95_seconds=float(run_time_p95 or 0.0),
        )
        for (
            queue_name,
            task_type,
            count,
            ready_wait_p50,
            ready_wait_p95,
            total_wait_p50,
            total_wait_p95,
            run_time_p50,
            run_time_p95,
        ) in rows
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
