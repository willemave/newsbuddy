"""Tests for queue health query read model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.schema import ProcessingTask
from app.queries.queue_health import get_queue_health_snapshot


def test_queue_health_reports_backlog_leases_retries_and_failures(db_session: Session) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add_all(
        [
            ProcessingTask(
                task_type=TaskType.SUMMARIZE.value,
                queue_name=TaskQueue.CONTENT.value,
                status=TaskStatus.PENDING.value,
                retry_count=0,
                created_at=now - timedelta(minutes=15),
                available_at=now - timedelta(minutes=10),
            ),
            ProcessingTask(
                task_type=TaskType.SUMMARIZE.value,
                queue_name=TaskQueue.CONTENT.value,
                status=TaskStatus.PENDING.value,
                retry_count=2,
                created_at=now - timedelta(minutes=5),
                available_at=now - timedelta(minutes=5),
            ),
            ProcessingTask(
                task_type=TaskType.GENERATE_IMAGE.value,
                queue_name=TaskQueue.IMAGE.value,
                status=TaskStatus.PROCESSING.value,
                lease_expires_at=now - timedelta(minutes=1),
            ),
            ProcessingTask(
                task_type=TaskType.PROCESS_CONTENT.value,
                queue_name=TaskQueue.CONTENT.value,
                status=TaskStatus.FAILED.value,
                error_message="extract failed",
                created_at=now - timedelta(hours=1),
                completed_at=now - timedelta(minutes=30),
            ),
        ]
    )
    db_session.commit()

    snapshot = get_queue_health_snapshot(db_session, window_hours=2)

    assert snapshot.processing_count == 1
    assert snapshot.expired_lease_count == 1
    assert snapshot.recent_failed_count == 1
    assert [(row.retry_count, row.pending_count) for row in snapshot.retry_buckets] == [
        (0, 1),
        (2, 1),
    ]
    assert snapshot.pending[0].queue_name == "content"
    assert snapshot.pending[0].task_type == "summarize"
    assert snapshot.pending[0].pending_count == 2
    assert snapshot.pending[0].oldest_pending_age_seconds is not None
    assert snapshot.top_failures[0].task_type == "process_content"
    assert snapshot.top_failures[0].error_message == "extract failed"
