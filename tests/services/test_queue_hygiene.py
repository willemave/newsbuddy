"""Focused coverage for queue retention and low-cost backpressure checks."""

from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.db import ProcessingTask
from app.services.queue import QueueService, cleanup_terminal_tasks_in_session


def _patch_db(monkeypatch, db_session) -> QueueService:
    @contextmanager
    def _get_db_override():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    monkeypatch.setattr("app.services.queue.get_db", _get_db_override)
    return QueueService()


def test_cleanup_terminal_tasks_preserves_active_recent_and_boundary_rows(
    db_session,
    monkeypatch,
) -> None:
    queue = _patch_db(monkeypatch, db_session)
    now = datetime(2026, 7, 25, 12, 0, 0)
    cutoff = now - timedelta(days=14)
    rows = [
        ProcessingTask(
            task_type=TaskType.SUMMARIZE.value,
            status=TaskStatus.COMPLETED.value,
            payload={},
            queue_name=TaskQueue.CONTENT.value,
            created_at=cutoff - timedelta(days=2),
            completed_at=cutoff - timedelta(seconds=1),
        ),
        ProcessingTask(
            task_type=TaskType.GENERATE_IMAGE.value,
            status=TaskStatus.FAILED.value,
            payload={},
            queue_name=TaskQueue.IMAGE.value,
            created_at=cutoff - timedelta(seconds=1),
            completed_at=None,
        ),
        ProcessingTask(
            task_type=TaskType.SUMMARIZE.value,
            status=TaskStatus.COMPLETED.value,
            payload={},
            queue_name=TaskQueue.CONTENT.value,
            created_at=cutoff - timedelta(days=2),
            completed_at=cutoff,
        ),
        ProcessingTask(
            task_type=TaskType.PROCESS_CONTENT.value,
            status=TaskStatus.PENDING.value,
            payload={},
            queue_name=TaskQueue.CONTENT.value,
            created_at=cutoff - timedelta(days=30),
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()
    expired_ids = {rows[0].id, rows[1].id}
    retained_ids = {rows[2].id, rows[3].id}

    result = queue.cleanup_terminal_tasks(retention_days=14, now=now)

    assert result == {
        "deleted_count": 2,
        "batch_count": 1,
        "batch_size": 5000,
        "max_delete": 50000,
        "has_more": False,
        "retention_days": 14,
        "cutoff": cutoff,
    }
    remaining_ids = {row.id for row in db_session.query(ProcessingTask.id).all()}
    assert remaining_ids.isdisjoint(expired_ids)
    assert retained_ids <= remaining_ids


def test_cleanup_terminal_tasks_commits_short_batches_and_stops_at_run_cap(
    db_session,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 25, 12, 0, 0)
    expired = [
        ProcessingTask(
            task_type=TaskType.SUMMARIZE.value,
            status=TaskStatus.COMPLETED.value,
            payload={},
            queue_name=TaskQueue.CONTENT.value,
            created_at=now - timedelta(days=20, seconds=index),
            completed_at=now - timedelta(days=19, seconds=index),
        )
        for index in range(7)
    ]
    db_session.add_all(expired)
    db_session.commit()

    commit_count = 0
    original_commit = db_session.commit

    def recording_commit() -> None:
        nonlocal commit_count
        commit_count += 1
        original_commit()

    monkeypatch.setattr(db_session, "commit", recording_commit)

    result = cleanup_terminal_tasks_in_session(
        db_session,
        retention_days=14,
        batch_size=2,
        max_delete=5,
        now=now,
    )

    assert result == {
        "deleted_count": 5,
        "batch_count": 3,
        "batch_size": 2,
        "max_delete": 5,
        "has_more": True,
        "retention_days": 14,
        "cutoff": now - timedelta(days=14),
    }
    assert commit_count == 3
    assert db_session.query(ProcessingTask).count() == 2


def test_backpressure_uses_one_content_only_aggregate(db_session, monkeypatch) -> None:
    queue = _patch_db(monkeypatch, db_session)
    monkeypatch.setattr(
        "app.services.queue.get_settings",
        lambda: SimpleNamespace(
            queue=SimpleNamespace(
                queue_backpressure_max_pending_content=3,
                queue_backpressure_max_pending_process_news_item=2,
            )
        ),
    )
    db_session.add_all(
        [
            ProcessingTask(
                task_type=TaskType.PROCESS_NEWS_ITEM.value,
                status=TaskStatus.PENDING.value,
                payload={},
                queue_name=TaskQueue.CONTENT.value,
            ),
            ProcessingTask(
                task_type=TaskType.SUMMARIZE.value,
                status=TaskStatus.PENDING.value,
                payload={},
                queue_name=TaskQueue.CONTENT.value,
            ),
            ProcessingTask(
                task_type=TaskType.PROCESS_NEWS_ITEM.value,
                status=TaskStatus.PENDING.value,
                payload={},
                queue_name=TaskQueue.IMAGE.value,
            ),
            ProcessingTask(
                task_type=TaskType.PROCESS_NEWS_ITEM.value,
                status=TaskStatus.COMPLETED.value,
                payload={},
                queue_name=TaskQueue.CONTENT.value,
            ),
        ]
    )
    db_session.commit()

    def fail_full_stats():
        raise AssertionError("backpressure must not aggregate the entire queue table")

    monkeypatch.setattr(queue, "get_queue_stats", fail_full_stats)

    result = queue.get_backpressure_status()

    assert result["should_throttle"] is False
    assert result["counts"] == {
        "pending_content": 2,
        "pending_process_news_item": 1,
    }


def test_processing_task_dequeue_columns_and_indexes_match_query_shape() -> None:
    table = ProcessingTask.__table__
    retry_count = table.c.retry_count
    assert retry_count.nullable is False
    assert retry_count.server_default is not None

    index_columns = {
        str(index.name): tuple(column.name for column in index.columns)
        for index in table.indexes
        if index.name is not None
    }
    assert index_columns["idx_task_status_available"] == (
        "status",
        "retry_count",
        "available_at",
        "created_at",
        "id",
    )
    assert index_columns["idx_task_queue_status_available"] == (
        "queue_name",
        "status",
        "retry_count",
        "available_at",
        "created_at",
        "id",
    )
