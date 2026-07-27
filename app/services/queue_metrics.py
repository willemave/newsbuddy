"""Read-only queue statistics and backpressure decisions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session

from app.core.settings import QueueSettingsView
from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.db import ProcessingTask


def get_queue_stats(db: Session) -> dict[str, Any]:
    """Return queue counts used by operators and health endpoints."""
    status_counts = (
        db.query(ProcessingTask.status, func.count(ProcessingTask.id))
        .group_by(ProcessingTask.status)
        .all()
    )
    type_counts = (
        db.query(ProcessingTask.task_type, func.count(ProcessingTask.id))
        .filter(ProcessingTask.status == TaskStatus.PENDING.value)
        .group_by(ProcessingTask.task_type)
        .all()
    )
    queue_counts = (
        db.query(ProcessingTask.queue_name, func.count(ProcessingTask.id))
        .filter(ProcessingTask.status == TaskStatus.PENDING.value)
        .group_by(ProcessingTask.queue_name)
        .all()
    )
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
    one_hour_ago = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
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

    pending_by_queue_type: dict[str, dict[str, int]] = {}
    for queue_name, task_type, count in queue_type_counts:
        pending_by_queue_type.setdefault(queue_name, {})[task_type] = count
    return {
        "by_status": {status: count for status, count in status_counts},
        "pending_by_type": {task_type: count for task_type, count in type_counts},
        "pending_by_queue": {queue_name: count for queue_name, count in queue_counts},
        "pending_by_queue_type": pending_by_queue_type,
        "recent_failures": recent_failures,
    }


def get_backpressure_pending_counts(db: Session) -> tuple[int, int]:
    """Count only pending content rows needed for throttle decisions."""
    content_pending, pending_process_news_item = (
        db.query(
            func.count(ProcessingTask.id),
            func.coalesce(
                func.sum(
                    case(
                        (ProcessingTask.task_type == TaskType.PROCESS_NEWS_ITEM.value, 1),
                        else_=0,
                    )
                ),
                0,
            ),
        )
        .filter(
            ProcessingTask.status == TaskStatus.PENDING.value,
            ProcessingTask.queue_name == TaskQueue.CONTENT.value,
        )
        .one()
    )
    return int(content_pending), int(pending_process_news_item)


def get_backpressure_status(db: Session, queue_settings: QueueSettingsView) -> dict[str, Any]:
    """Return whether pending backlog is healthy enough for cron enqueue work."""
    content_pending, pending_process_news_item = get_backpressure_pending_counts(db)
    reasons: list[str] = []
    if content_pending >= queue_settings.queue_backpressure_max_pending_content:
        reasons.append("content_queue_backlog")
    if pending_process_news_item >= queue_settings.queue_backpressure_max_pending_process_news_item:
        reasons.append("process_news_item_backlog")
    return {
        "should_throttle": bool(reasons),
        "reasons": reasons,
        "counts": {
            "pending_content": content_pending,
            "pending_process_news_item": pending_process_news_item,
        },
        "thresholds": {
            "pending_content": queue_settings.queue_backpressure_max_pending_content,
            "pending_process_news_item": (
                queue_settings.queue_backpressure_max_pending_process_news_item
            ),
        },
    }
