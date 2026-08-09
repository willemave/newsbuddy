"""Tests for the queue watchdog recovery script."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

from sqlalchemy.exc import OperationalError

from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.db import ProcessingTask
from scripts.watchdog_queue_recovery import (
    ActionResult,
    WatchdogRunResult,
    _parse_args,
    main,
    run_watchdog_once,
)


def test_run_watchdog_once_requeues_stale_process_news_item(db_session) -> None:
    """Watchdog should requeue stale process_news_item tasks."""
    locked_at = datetime.now(UTC) - timedelta(hours=3)
    stale_task = ProcessingTask(
        task_type=TaskType.PROCESS_NEWS_ITEM.value,
        status=TaskStatus.PROCESSING.value,
        payload={"news_item_id": 123},
        queue_name=TaskQueue.CONTENT.value,
        retry_count=0,
        started_at=locked_at,
        locked_at=locked_at,
        locked_by="stale-worker",
        lease_token=uuid4(),  # type: ignore[arg-type]  # SQLAlchemy's Column constructor loses the UUID type here.
        lease_expires_at=locked_at + timedelta(minutes=5),
    )
    db_session.add(stale_task)
    db_session.commit()

    result = run_watchdog_once(
        session=db_session,
        transcribe_stale_hours=2.0,
        process_content_stale_hours=2.0,
        process_news_item_stale_hours=2.0,
        alert_threshold=99,
        slack_webhook_url=None,
        dry_run=False,
        action_limit=None,
    )
    db_session.commit()
    db_session.refresh(stale_task)

    assert result.requeued_process_news_item.touched_count == 1
    assert stale_task.status == TaskStatus.PENDING.value
    assert stale_task.started_at is None
    assert stale_task.completed_at is None
    assert stale_task.retry_count == 1
    assert stale_task.locked_at is None
    assert stale_task.locked_by is None
    assert stale_task.lease_token is None
    assert stale_task.lease_expires_at is None


def test_run_watchdog_once_requeues_stale_sync_integration(db_session) -> None:
    """Watchdog should requeue stale sync_integration tasks."""
    stale_task = ProcessingTask(
        task_type=TaskType.SYNC_INTEGRATION.value,
        status=TaskStatus.PROCESSING.value,
        payload={"user_id": 1, "provider": "x", "trigger": "cron"},
        queue_name=TaskQueue.TWITTER.value,
        retry_count=0,
        started_at=datetime.now(UTC) - timedelta(hours=3),
        locked_at=None,
        locked_by=None,
        error_message="old X API failure",
    )
    db_session.add(stale_task)
    db_session.commit()

    result = run_watchdog_once(
        session=db_session,
        transcribe_stale_hours=2.0,
        process_content_stale_hours=2.0,
        process_news_item_stale_hours=2.0,
        sync_integration_stale_hours=2.0,
        alert_threshold=99,
        slack_webhook_url=None,
        dry_run=False,
        action_limit=None,
    )
    db_session.commit()
    db_session.refresh(stale_task)

    assert result.requeued_sync_integration.touched_count == 1
    assert stale_task.status == TaskStatus.PENDING.value
    assert stale_task.started_at is None
    assert stale_task.completed_at is None
    assert stale_task.error_message is None
    assert stale_task.retry_count == 1


def test_run_watchdog_once_moves_misrouted_queue_rows(db_session) -> None:
    """Watchdog should normalize active rows created before queue splits."""
    discussion_task = ProcessingTask(
        task_type=TaskType.FETCH_NEWS_ITEM_DISCUSSION.value,
        status=TaskStatus.PENDING.value,
        payload={"news_item_id": 123},
        queue_name=TaskQueue.CONTENT.value,
    )
    backfill_task = ProcessingTask(
        task_type=TaskType.BACKFILL_FEEDS.value,
        status=TaskStatus.PENDING.value,
        payload={"user_id": 1, "config_ids": [2], "count": 10},
        queue_name=TaskQueue.CONTENT.value,
    )
    audio_episode_task = ProcessingTask(
        task_type=TaskType.GENERATE_AUDIO_EPISODE.value,
        status=TaskStatus.PROCESSING.value,
        payload={"audio_episode_id": 4},
        queue_name=TaskQueue.MEDIA.value,
    )
    completed_misrouted_task = ProcessingTask(
        task_type="fetch_discussion",
        status=TaskStatus.COMPLETED.value,
        payload={},
        queue_name=TaskQueue.CONTENT.value,
    )
    db_session.add_all(
        [
            discussion_task,
            backfill_task,
            audio_episode_task,
            completed_misrouted_task,
        ]
    )
    db_session.commit()

    result = run_watchdog_once(
        session=db_session,
        transcribe_stale_hours=2.0,
        process_content_stale_hours=2.0,
        process_news_item_stale_hours=2.0,
        sync_integration_stale_hours=2.0,
        alert_threshold=99,
        slack_webhook_url=None,
        dry_run=False,
        action_limit=None,
    )
    db_session.commit()
    db_session.refresh(discussion_task)
    db_session.refresh(backfill_task)
    db_session.refresh(audio_episode_task)
    db_session.refresh(completed_misrouted_task)

    assert result.moved_misrouted.action_name == "move_misrouted"
    assert result.moved_misrouted.touched_count == 3
    assert discussion_task.queue_name == TaskQueue.DISCUSSION.value
    assert backfill_task.queue_name == TaskQueue.BACKFILL.value
    assert audio_episode_task.queue_name == TaskQueue.AUDIO_EPISODE.value
    assert completed_misrouted_task.queue_name == TaskQueue.CONTENT.value


def test_parse_args_supports_process_news_item_stale_hours() -> None:
    """CLI parsing should expose the news-item stale-hours option."""
    args = _parse_args(["--process-news-item-stale-hours", "6"])
    assert args.process_news_item_stale_hours == 6.0


def test_parse_args_supports_sync_integration_stale_hours() -> None:
    """CLI parsing should expose the sync-integration stale-hours option."""
    args = _parse_args(["--sync-integration-stale-hours", "7"])
    assert args.sync_integration_stale_hours == 7.0


def test_main_retries_transient_operational_error() -> None:
    """Transient DB recovery errors should be retried before failing the watchdog."""
    session = MagicMock()
    session_context = MagicMock()
    session_context.__enter__.return_value = session
    session_context.__exit__.return_value = None
    session_factory = MagicMock(return_value=session_context)
    engine = MagicMock()
    result = WatchdogRunResult(
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        dry_run=False,
        moved_misrouted=ActionResult("move_misrouted", 0, [], {}),
        requeued_media=ActionResult("requeue_stale_media", 0, [], {}),
        requeued_process_content=ActionResult("requeue_stale_process_content", 0, [], {}),
        requeued_process_news_item=ActionResult("requeue_stale_process_news_item", 0, [], {}),
        requeued_sync_integration=ActionResult("requeue_stale_sync_integration", 0, [], {}),
    )

    with (
        patch(
            "scripts.watchdog_queue_recovery._create_session_factory",
            return_value=(session_factory, engine, "postgresql://example/newsly"),
        ),
        patch(
            "scripts.watchdog_queue_recovery.run_watchdog_once",
            side_effect=[
                OperationalError(
                    "SELECT processing_tasks.id",
                    {},
                    Exception("server closed the connection unexpectedly"),
                ),
                result,
            ],
        ),
        patch("scripts.watchdog_queue_recovery._print_result"),
        patch("scripts.watchdog_queue_recovery.setup_logging"),
        patch("scripts.watchdog_queue_recovery.time.sleep") as mock_sleep,
    ):
        exit_code = main([])

    assert exit_code == 0
    assert session.rollback.call_count == 1
    engine.dispose.assert_called()
    mock_sleep.assert_any_call(5)
