"""Tests for queue health query read model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pytest import approx
from sqlalchemy.orm import Session

from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.db import ProcessingTask
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
                task_type=TaskType.FETCH_NEWS_ITEM_DISCUSSION.value,
                queue_name=TaskQueue.DISCUSSION.value,
                status=TaskStatus.PENDING.value,
                retry_count=0,
                created_at=now - timedelta(minutes=35),
                available_at=now - timedelta(minutes=30),
            ),
            ProcessingTask(
                task_type=TaskType.FETCH_NEWS_ITEM_DISCUSSION.value,
                queue_name=TaskQueue.DISCUSSION.value,
                status=TaskStatus.PENDING.value,
                retry_count=0,
                created_at=now - timedelta(minutes=34),
                available_at=now - timedelta(minutes=29),
            ),
            ProcessingTask(
                task_type=TaskType.FETCH_NEWS_ITEM_DISCUSSION.value,
                queue_name=TaskQueue.DISCUSSION.value,
                status=TaskStatus.COMPLETED.value,
                retry_count=0,
                created_at=now - timedelta(minutes=33),
                available_at=now - timedelta(minutes=33),
                completed_at=now - timedelta(minutes=32),
            ),
            ProcessingTask(
                task_type=TaskType.GENERATE_IMAGE.value,
                queue_name=TaskQueue.IMAGE.value,
                status=TaskStatus.PROCESSING.value,
                started_at=now - timedelta(minutes=12),
                lease_expires_at=now - timedelta(minutes=1),
            ),
            ProcessingTask(
                task_type=TaskType.GENERATE_AUDIO_EPISODE.value,
                queue_name=TaskQueue.AUDIO_EPISODE.value,
                status=TaskStatus.PROCESSING.value,
                started_at=now - timedelta(minutes=20),
                lease_expires_at=now + timedelta(minutes=5),
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

    assert snapshot.processing_count == 2
    assert snapshot.expired_lease_count == 1
    assert len(snapshot.processing) == 2
    assert snapshot.processing[0].queue_name == "audio_episode"
    assert snapshot.processing[0].task_type == "generate_audio_episode"
    assert snapshot.processing[0].processing_count == 1
    assert snapshot.processing[0].oldest_processing_age_seconds is not None
    assert snapshot.recent_failed_count == 1
    assert [(row.retry_count, row.pending_count) for row in snapshot.retry_buckets] == [
        (0, 3),
        (2, 1),
    ]
    assert snapshot.pending[0].queue_name == "discussion"
    assert snapshot.pending[0].task_type == "fetch_news_item_discussion"
    assert snapshot.pending[0].pending_count == 2
    assert snapshot.pending[0].oldest_pending_age_seconds is not None
    assert snapshot.pending[1].queue_name == "content"
    assert snapshot.pending[1].task_type == "summarize"
    assert snapshot.top_failures[0].task_type == "process_content"
    assert snapshot.top_failures[0].error_message == "extract failed"
    assert snapshot.activity[0].queue_name == "discussion"
    assert snapshot.activity[0].task_type == "fetch_news_item_discussion"
    assert snapshot.activity[0].enqueued_count == 3
    activity = {(row.queue_name, row.task_type): row for row in snapshot.activity}
    assert activity[("content", "summarize")].enqueued_count == 2
    assert activity[("content", "process_content")].failed_count == 1
    assert activity[("image", "generate_image")].enqueued_count == 1


def test_queue_health_reports_terminal_task_latency_percentiles(db_session: Session) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    samples = [
        (10, 20, 30, TaskStatus.COMPLETED.value),
        (20, 40, 60, TaskStatus.COMPLETED.value),
        (30, 60, 90, TaskStatus.FAILED.value),
        (40, 80, 120, TaskStatus.COMPLETED.value),
    ]
    for ready_wait, total_wait, run_time, status in samples:
        started_at = now - timedelta(minutes=10)
        db_session.add(
            ProcessingTask(
                task_type=TaskType.SUMMARIZE.value,
                queue_name=TaskQueue.CONTENT.value,
                status=status,
                created_at=started_at - timedelta(seconds=total_wait),
                available_at=started_at - timedelta(seconds=ready_wait),
                started_at=started_at,
                completed_at=started_at + timedelta(seconds=run_time),
                error_message="model failed" if status == TaskStatus.FAILED.value else None,
            )
        )

    db_session.add_all(
        [
            ProcessingTask(
                task_type=TaskType.SUMMARIZE.value,
                queue_name=TaskQueue.CONTENT.value,
                status=TaskStatus.COMPLETED.value,
                created_at=now - timedelta(hours=4),
                available_at=now - timedelta(hours=4),
                started_at=now - timedelta(hours=3, minutes=30),
                completed_at=now - timedelta(hours=3),
            ),
            ProcessingTask(
                task_type=TaskType.SUMMARIZE.value,
                queue_name=TaskQueue.CONTENT.value,
                status=TaskStatus.COMPLETED.value,
                created_at=now - timedelta(minutes=5),
                available_at=now - timedelta(minutes=5),
                started_at=None,
                completed_at=now,
            ),
            ProcessingTask(
                task_type=TaskType.ANALYZE_URL.value,
                queue_name=TaskQueue.CONTENT.value,
                status=TaskStatus.COMPLETED.value,
                created_at=now,
                available_at=now,
                started_at=now - timedelta(seconds=30),
                completed_at=now - timedelta(seconds=45),
            ),
        ]
    )
    db_session.commit()

    snapshot = get_queue_health_snapshot(db_session, window_hours=2)
    latency = {(row.queue_name, row.task_type): row for row in snapshot.latency}

    summarize = latency[(TaskQueue.CONTENT.value, TaskType.SUMMARIZE.value)]
    assert summarize.sample_count == 4
    assert summarize.ready_wait_p50_seconds == approx(25)
    assert summarize.ready_wait_p95_seconds == approx(38.5)
    assert summarize.total_wait_p50_seconds == approx(50)
    assert summarize.total_wait_p95_seconds == approx(77)
    assert summarize.run_time_p50_seconds == approx(75)
    assert summarize.run_time_p95_seconds == approx(115.5)

    clamped = latency[(TaskQueue.CONTENT.value, TaskType.ANALYZE_URL.value)]
    assert clamped.sample_count == 1
    assert clamped.ready_wait_p50_seconds == 0
    assert clamped.total_wait_p50_seconds == 0
    assert clamped.run_time_p50_seconds == 0
