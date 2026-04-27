"""Tests for queue service behavior."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.schema import ProcessingTask
from app.services.queue import QueueService, TaskQueue, TaskStatus, TaskType


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


def test_complete_task_sets_default_error_message(db_session, monkeypatch):
    """QueueService fills a default error message when missing."""
    queue = _patch_db(monkeypatch, db_session)

    task = ProcessingTask(
        task_type=TaskType.PROCESS_CONTENT.value,
        content_id=1,
        payload={},
        status=TaskStatus.PENDING.value,
        queue_name=TaskQueue.CONTENT.value,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    queue.complete_task(task.id, success=False, error_message=None)

    refreshed = db_session.query(ProcessingTask).filter(ProcessingTask.id == task.id).first()
    assert refreshed is not None
    assert refreshed.status == TaskStatus.FAILED.value
    assert refreshed.error_message == "Task failed without error details"


def test_finalize_task_retryable_failure_requeues_in_one_step(db_session, monkeypatch):
    """Retryable failures should go back to pending without a second queue write."""
    queue = _patch_db(monkeypatch, db_session)

    task = ProcessingTask(
        task_type=TaskType.GENERATE_AGENT_DIGEST.value,
        payload={"user_id": 1},
        status=TaskStatus.PROCESSING.value,
        queue_name=TaskQueue.CONTENT.value,
        retry_count=0,
        started_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    original_created_at = task.created_at

    transition = queue.finalize_task(
        task.id,
        success=False,
        error_message="database is locked",
        retryable=True,
        current_retry_count=task.retry_count,
        max_retries=3,
        retry_delay_seconds=120,
    )

    db_session.refresh(task)
    assert transition is not None
    assert task.status == TaskStatus.PENDING.value
    assert task.retry_count == 1
    assert task.started_at is None
    assert task.completed_at is None
    assert task.error_message == "database is locked"
    assert task.locked_at is None
    assert task.locked_by is None
    assert task.lease_expires_at is None
    assert task.created_at == original_created_at
    assert task.available_at >= datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=110)


def test_enqueue_assigns_default_queue_by_task_type(db_session, monkeypatch):
    """Tasks are partitioned into the expected default queue."""
    queue = _patch_db(monkeypatch, db_session)

    content_task_id = queue.enqueue(TaskType.SUMMARIZE, content_id=1)
    image_task_id = queue.enqueue(TaskType.GENERATE_IMAGE, content_id=9)
    transcribe_task_id = queue.enqueue(TaskType.TRANSCRIBE, content_id=2)
    tweet_video_task_id = queue.enqueue(TaskType.DOWNLOAD_TWEET_VIDEO_AUDIO, content_id=4)
    onboarding_task_id = queue.enqueue(TaskType.ONBOARDING_DISCOVER, payload={"user_id": 11})
    integration_task_id = queue.enqueue(
        TaskType.SYNC_INTEGRATION,
        payload={"user_id": 11, "provider": "x"},
    )
    agent_digest_task_id = queue.enqueue(
        TaskType.GENERATE_AGENT_DIGEST,
        payload={"user_id": 11, "trigger_reason": "scheduled"},
    )
    chat_task_id = queue.enqueue(
        TaskType.DIG_DEEPER,
        content_id=3,
        payload={"user_id": 11},
    )

    tasks = {
        task.id: task
        for task in db_session.query(ProcessingTask)
        .filter(
            ProcessingTask.id.in_(
                [
                    content_task_id,
                    image_task_id,
                    transcribe_task_id,
                    tweet_video_task_id,
                    onboarding_task_id,
                    integration_task_id,
                    agent_digest_task_id,
                    chat_task_id,
                ]
            )
        )
        .all()
    }

    assert tasks[content_task_id].queue_name == TaskQueue.CONTENT.value
    assert tasks[image_task_id].queue_name == TaskQueue.IMAGE.value
    assert tasks[transcribe_task_id].queue_name == TaskQueue.MEDIA.value
    assert tasks[tweet_video_task_id].queue_name == TaskQueue.MEDIA.value
    assert tasks[onboarding_task_id].queue_name == TaskQueue.ONBOARDING.value
    assert tasks[integration_task_id].queue_name == TaskQueue.TWITTER.value
    assert tasks[agent_digest_task_id].queue_name == TaskQueue.CONTENT.value
    assert tasks[chat_task_id].queue_name == TaskQueue.CHAT.value


def test_enqueue_dedupes_content_tasks_by_default(db_session, monkeypatch):
    """Content tasks reuse an existing pending/processing task for the same content."""
    queue = _patch_db(monkeypatch, db_session)

    first_task_id = queue.enqueue(TaskType.SUMMARIZE, content_id=42)
    second_task_id = queue.enqueue(TaskType.SUMMARIZE, content_id=42)
    assert second_task_id == first_task_id

    queued_tasks = (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.SUMMARIZE.value)
        .filter(ProcessingTask.content_id == 42)
        .all()
    )
    assert len(queued_tasks) == 1


def test_enqueue_validates_payload_against_task_spec(db_session, monkeypatch):
    queue = _patch_db(monkeypatch, db_session)

    with pytest.raises(ValueError, match="Invalid payload for analyze_url"):
        queue.enqueue(TaskType.ANALYZE_URL, payload={"content_id": "not-an-int"})


def test_enqueue_does_not_dedupe_onboarding_tasks(db_session, monkeypatch):
    """Non-content tasks can enqueue multiple pending jobs with different payloads."""
    queue = _patch_db(monkeypatch, db_session)

    first_task_id = queue.enqueue(TaskType.ONBOARDING_DISCOVER, payload={"user_id": 1})
    second_task_id = queue.enqueue(TaskType.ONBOARDING_DISCOVER, payload={"user_id": 2})
    assert second_task_id != first_task_id

    queued_tasks = (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.ONBOARDING_DISCOVER.value)
        .all()
    )
    assert len(queued_tasks) == 2


def test_dequeue_filters_by_queue_name(db_session, monkeypatch):
    """Dequeuing a queue partition never returns tasks from another queue."""
    queue = _patch_db(monkeypatch, db_session)

    db_session.add_all(
        [
            ProcessingTask(
                task_type=TaskType.SUMMARIZE.value,
                status=TaskStatus.PENDING.value,
                payload={},
                queue_name=TaskQueue.CONTENT.value,
            ),
            ProcessingTask(
                task_type=TaskType.TRANSCRIBE.value,
                status=TaskStatus.PENDING.value,
                payload={},
                queue_name=TaskQueue.MEDIA.value,
            ),
            ProcessingTask(
                task_type=TaskType.ONBOARDING_DISCOVER.value,
                status=TaskStatus.PENDING.value,
                payload={"user_id": 1},
                queue_name=TaskQueue.ONBOARDING.value,
            ),
            ProcessingTask(
                task_type=TaskType.DIG_DEEPER.value,
                status=TaskStatus.PENDING.value,
                payload={"user_id": 1},
                queue_name=TaskQueue.CHAT.value,
            ),
            ProcessingTask(
                task_type=TaskType.SYNC_INTEGRATION.value,
                status=TaskStatus.PENDING.value,
                payload={"user_id": 1, "provider": "x"},
                queue_name=TaskQueue.TWITTER.value,
            ),
        ]
    )
    db_session.commit()

    onboarding_task = queue.dequeue(
        worker_id="onboarding-test",
        queue_name=TaskQueue.ONBOARDING,
    )
    assert onboarding_task is not None
    assert onboarding_task["task_type"] == TaskType.ONBOARDING_DISCOVER.value
    assert onboarding_task["queue_name"] == TaskQueue.ONBOARDING.value

    second_onboarding_task = queue.dequeue(
        worker_id="onboarding-test",
        queue_name=TaskQueue.ONBOARDING,
    )
    assert second_onboarding_task is None

    content_task = queue.dequeue(worker_id="content-test", queue_name=TaskQueue.CONTENT)
    assert content_task is not None
    assert content_task["task_type"] == TaskType.SUMMARIZE.value
    assert content_task["queue_name"] == TaskQueue.CONTENT.value

    transcribe_task = queue.dequeue(worker_id="media-test", queue_name=TaskQueue.MEDIA)
    assert transcribe_task is not None
    assert transcribe_task["task_type"] == TaskType.TRANSCRIBE.value
    assert transcribe_task["queue_name"] == TaskQueue.MEDIA.value

    chat_task = queue.dequeue(worker_id="chat-test", queue_name=TaskQueue.CHAT)
    assert chat_task is not None
    assert chat_task["task_type"] == TaskType.DIG_DEEPER.value
    assert chat_task["queue_name"] == TaskQueue.CHAT.value

    twitter_task = queue.dequeue(worker_id="twitter-test", queue_name=TaskQueue.TWITTER)
    assert twitter_task is not None
    assert twitter_task["task_type"] == TaskType.SYNC_INTEGRATION.value
    assert twitter_task["queue_name"] == TaskQueue.TWITTER.value


def test_dequeue_claims_task_with_lease_metadata(db_session, monkeypatch):
    """Dequeued tasks should be marked processing with worker lease metadata."""
    queue = _patch_db(monkeypatch, db_session)
    now = datetime.now(UTC).replace(tzinfo=None)

    task = ProcessingTask(
        task_type=TaskType.SUMMARIZE.value,
        status=TaskStatus.PENDING.value,
        payload={},
        queue_name=TaskQueue.CONTENT.value,
        available_at=now - timedelta(seconds=1),
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    dequeued = queue.dequeue(worker_id="worker-lease", queue_name=TaskQueue.CONTENT)

    assert dequeued is not None
    assert dequeued["id"] == task.id
    assert dequeued["status"] == TaskStatus.PROCESSING.value
    assert dequeued["locked_by"] == "worker-lease"
    assert dequeued["locked_at"] is not None
    assert dequeued["lease_expires_at"] is not None
    assert dequeued["available_at"] == task.available_at

    refreshed = db_session.query(ProcessingTask).filter(ProcessingTask.id == task.id).first()
    assert refreshed is not None
    assert refreshed.status == TaskStatus.PROCESSING.value
    assert refreshed.locked_by == "worker-lease"
    assert refreshed.locked_at is not None
    assert refreshed.lease_expires_at is not None


def test_dequeue_reclaims_expired_processing_task(db_session, monkeypatch):
    """Expired processing leases should be reclaimable by another worker."""
    queue = _patch_db(monkeypatch, db_session)
    now = datetime.now(UTC).replace(tzinfo=None)

    task = ProcessingTask(
        task_type=TaskType.SUMMARIZE.value,
        status=TaskStatus.PROCESSING.value,
        payload={},
        queue_name=TaskQueue.CONTENT.value,
        available_at=now - timedelta(minutes=10),
        started_at=now - timedelta(minutes=10),
        locked_at=now - timedelta(minutes=10),
        locked_by="stale-worker",
        lease_expires_at=now - timedelta(seconds=1),
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    dequeued = queue.dequeue(worker_id="worker-reclaim", queue_name=TaskQueue.CONTENT)

    assert dequeued is not None
    assert dequeued["id"] == task.id
    assert dequeued["locked_by"] == "worker-reclaim"
    assert dequeued["lease_expires_at"] is not None

    refreshed = db_session.query(ProcessingTask).filter(ProcessingTask.id == task.id).first()
    assert refreshed is not None
    assert refreshed.status == TaskStatus.PROCESSING.value
    assert refreshed.locked_by == "worker-reclaim"
    assert refreshed.lease_expires_at is not None
    assert refreshed.lease_expires_at > now


def test_renew_lease_extends_processing_task(db_session, monkeypatch):
    """Renewing a lease should extend the expiration for the owning worker."""
    queue = _patch_db(monkeypatch, db_session)
    now = datetime.now(UTC).replace(tzinfo=None)

    task = ProcessingTask(
        task_type=TaskType.SUMMARIZE.value,
        status=TaskStatus.PROCESSING.value,
        payload={},
        queue_name=TaskQueue.CONTENT.value,
        available_at=now - timedelta(minutes=1),
        started_at=now - timedelta(minutes=1),
        locked_at=now - timedelta(seconds=30),
        locked_by="worker-lease",
        lease_expires_at=now + timedelta(seconds=10),
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    original_expiry = task.lease_expires_at
    renewed = queue.renew_lease(task.id, worker_id="worker-lease", lease_seconds=120)

    assert renewed is True
    db_session.refresh(task)
    assert task.locked_by == "worker-lease"
    assert task.lease_expires_at is not None
    assert original_expiry is not None
    assert task.lease_expires_at > original_expiry


def test_get_queue_stats_reports_pending_by_queue(db_session, monkeypatch):
    """Queue stats include pending totals grouped by queue and queue/type."""
    queue = _patch_db(monkeypatch, db_session)

    db_session.add_all(
        [
            ProcessingTask(
                task_type=TaskType.SUMMARIZE.value,
                status=TaskStatus.PENDING.value,
                payload={},
                queue_name=TaskQueue.CONTENT.value,
            ),
            ProcessingTask(
                task_type=TaskType.GENERATE_IMAGE.value,
                status=TaskStatus.PENDING.value,
                payload={},
                queue_name=TaskQueue.IMAGE.value,
            ),
            ProcessingTask(
                task_type=TaskType.ONBOARDING_DISCOVER.value,
                status=TaskStatus.PENDING.value,
                payload={"user_id": 1},
                queue_name=TaskQueue.ONBOARDING.value,
            ),
            ProcessingTask(
                task_type=TaskType.DIG_DEEPER.value,
                status=TaskStatus.COMPLETED.value,
                payload={"user_id": 1},
                queue_name=TaskQueue.CHAT.value,
            ),
        ]
    )
    db_session.commit()

    stats = queue.get_queue_stats()

    assert stats["pending_by_queue"][TaskQueue.CONTENT.value] == 1
    assert stats["pending_by_queue"][TaskQueue.IMAGE.value] == 1
    assert stats["pending_by_queue"][TaskQueue.ONBOARDING.value] == 1
    assert TaskQueue.CHAT.value not in stats["pending_by_queue"]
    assert stats["pending_by_queue_type"][TaskQueue.CONTENT.value][TaskType.SUMMARIZE.value] == 1
    assert stats["pending_by_queue_type"][TaskQueue.IMAGE.value][TaskType.GENERATE_IMAGE.value] == 1
    assert (
        stats["pending_by_queue_type"][TaskQueue.ONBOARDING.value][
            TaskType.ONBOARDING_DISCOVER.value
        ]
        == 1
    )


def test_get_backpressure_status_uses_content_thresholds(db_session, monkeypatch):
    """Backpressure status should reflect configured content queue thresholds."""
    queue = _patch_db(monkeypatch, db_session)
    monkeypatch.setattr(
        "app.services.queue.get_settings",
        lambda: SimpleNamespace(
            queue=SimpleNamespace(
                queue_backpressure_max_pending_content=2,
                queue_backpressure_max_pending_process_news_item=1,
                queue_backpressure_max_pending_generate_agent_digest=1,
            ),
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
                task_type=TaskType.GENERATE_AGENT_DIGEST.value,
                status=TaskStatus.PENDING.value,
                payload={},
                queue_name=TaskQueue.CONTENT.value,
            ),
        ]
    )
    db_session.commit()

    status = queue.get_backpressure_status()

    assert status["should_throttle"] is True
    assert status["counts"]["pending_content"] == 2
    assert status["counts"]["pending_process_news_item"] == 1
    assert status["counts"]["pending_generate_agent_digest"] == 1
    assert "content_queue_backlog" in status["reasons"]
    assert "process_news_item_backlog" in status["reasons"]
    assert "generate_agent_digest_backlog" in status["reasons"]


def test_dequeue_respects_retry_delay_schedule(db_session, monkeypatch):
    """Tasks scheduled for future retry are not dequeued early."""
    queue = _patch_db(monkeypatch, db_session)
    now = datetime.now(UTC).replace(tzinfo=None)

    ready_task = ProcessingTask(
        task_type=TaskType.SUMMARIZE.value,
        status=TaskStatus.PENDING.value,
        payload={},
        queue_name=TaskQueue.CONTENT.value,
        created_at=now - timedelta(seconds=1),
        available_at=now - timedelta(seconds=1),
    )
    delayed_task = ProcessingTask(
        task_type=TaskType.SUMMARIZE.value,
        status=TaskStatus.PENDING.value,
        payload={},
        queue_name=TaskQueue.CONTENT.value,
        created_at=now,
        available_at=now + timedelta(minutes=5),
    )
    db_session.add_all([ready_task, delayed_task])
    db_session.commit()

    first = queue.dequeue(worker_id="worker-a", queue_name=TaskQueue.CONTENT)
    second = queue.dequeue(worker_id="worker-b", queue_name=TaskQueue.CONTENT)

    assert first is not None
    assert first["id"] == ready_task.id
    assert second is None


def test_dequeue_rotates_retry_buckets(db_session, monkeypatch):
    """Dequeue should rotate available retry buckets to avoid starvation."""
    queue = _patch_db(monkeypatch, db_session)
    now = datetime.now(UTC).replace(tzinfo=None)

    retry_zero_oldest = ProcessingTask(
        task_type=TaskType.SUMMARIZE.value,
        status=TaskStatus.PENDING.value,
        payload={},
        queue_name=TaskQueue.CONTENT.value,
        retry_count=0,
        created_at=now - timedelta(minutes=10),
        available_at=now - timedelta(minutes=10),
    )
    retry_zero_newer = ProcessingTask(
        task_type=TaskType.SUMMARIZE.value,
        status=TaskStatus.PENDING.value,
        payload={},
        queue_name=TaskQueue.CONTENT.value,
        retry_count=0,
        created_at=now - timedelta(minutes=5),
        available_at=now - timedelta(minutes=5),
    )
    retry_one_task = ProcessingTask(
        task_type=TaskType.SUMMARIZE.value,
        status=TaskStatus.PENDING.value,
        payload={},
        queue_name=TaskQueue.CONTENT.value,
        retry_count=1,
        created_at=now - timedelta(minutes=20),
        available_at=now - timedelta(minutes=20),
    )
    db_session.add_all([retry_zero_oldest, retry_zero_newer, retry_one_task])
    db_session.commit()

    first = queue.dequeue(worker_id="worker-a", queue_name=TaskQueue.CONTENT)
    second = queue.dequeue(worker_id="worker-b", queue_name=TaskQueue.CONTENT)

    assert first is not None
    assert second is not None
    assert first["id"] == retry_zero_oldest.id
    assert second["id"] == retry_one_task.id
