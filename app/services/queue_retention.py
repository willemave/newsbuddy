"""Bounded retention cleanup for terminal processing tasks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.models.contracts import TaskStatus
from app.models.db import ProcessingTask

DEFAULT_TASK_RETENTION_DAYS = 14
DEFAULT_TASK_CLEANUP_BATCH_SIZE = 5_000
DEFAULT_TASK_CLEANUP_MAX_DELETE = 50_000
TERMINAL_TASK_STATUSES: tuple[str, str] = (
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
)


def build_terminal_task_retention_filter(cutoff: datetime):
    """Return the shared predicate for expired terminal task rows."""
    return and_(
        ProcessingTask.status.in_(TERMINAL_TASK_STATUSES),
        or_(
            ProcessingTask.completed_at < cutoff,
            and_(
                ProcessingTask.completed_at.is_(None),
                ProcessingTask.created_at < cutoff,
            ),
        ),
    )


def cleanup_terminal_tasks_in_session(
    db: Session,
    *,
    retention_days: int = DEFAULT_TASK_RETENTION_DAYS,
    batch_size: int = DEFAULT_TASK_CLEANUP_BATCH_SIZE,
    max_delete: int = DEFAULT_TASK_CLEANUP_MAX_DELETE,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Delete expired terminal tasks in short commits up to one run cap."""
    effective_retention_days = max(int(retention_days), 1)
    effective_max_delete = max(int(max_delete), 1)
    effective_batch_size = min(max(int(batch_size), 1), effective_max_delete)
    current_time = now or datetime.now(UTC).replace(tzinfo=None)
    cutoff = current_time - timedelta(days=effective_retention_days)
    retention_filter = build_terminal_task_retention_filter(cutoff)
    deleted_count = 0
    batch_count = 0

    while deleted_count < effective_max_delete:
        current_batch_size = min(effective_batch_size, effective_max_delete - deleted_count)
        task_ids = list(
            db.scalars(
                select(ProcessingTask.id)
                .where(retention_filter)
                .order_by(ProcessingTask.id.asc())
                .limit(current_batch_size)
            )
        )
        if not task_ids:
            break

        delete_result = db.execute(delete(ProcessingTask).where(ProcessingTask.id.in_(task_ids)))
        raw_rowcount = getattr(delete_result, "rowcount", None)
        deleted_in_batch = (
            len(task_ids) if raw_rowcount is None or int(raw_rowcount) < 0 else int(raw_rowcount)
        )
        db.commit()
        batch_count += 1
        deleted_count += deleted_in_batch
        if len(task_ids) < current_batch_size:
            break

    has_more = False
    if deleted_count >= effective_max_delete:
        has_more = db.scalar(select(ProcessingTask.id).where(retention_filter).limit(1)) is not None

    return {
        "deleted_count": deleted_count,
        "batch_count": batch_count,
        "batch_size": effective_batch_size,
        "max_delete": effective_max_delete,
        "has_more": has_more,
        "retention_days": effective_retention_days,
        "cutoff": cutoff,
    }
