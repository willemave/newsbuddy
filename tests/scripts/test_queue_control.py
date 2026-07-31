"""Tests for queue_control maintenance commands."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.db import ProcessingTask
from scripts import queue_control


def test_requeue_stale_processing_clears_exact_claim(db_session) -> None:
    locked_at = datetime.now(UTC) - timedelta(hours=3)
    task = ProcessingTask(
        task_type=TaskType.SYNC_INTEGRATION.value,
        payload={"provider": "x"},
        status=TaskStatus.PROCESSING.value,
        queue_name=TaskQueue.TWITTER.value,
        retry_count=0,
        started_at=locked_at,
        locked_at=locked_at,
        locked_by="stale-worker",
        lease_token=uuid4(),  # type: ignore[arg-type]  # SQLAlchemy's Column constructor loses the UUID type here.
        lease_expires_at=locked_at + timedelta(minutes=5),
    )
    db_session.add(task)
    db_session.commit()

    queue_control.requeue_stale_processing(
        db_session,
        hours=2,
        queue_name=TaskQueue.TWITTER.value,
        task_type=TaskType.SYNC_INTEGRATION.value,
        dry_run=False,
        force=True,
    )

    db_session.refresh(task)
    assert task.status == TaskStatus.PENDING.value
    assert task.retry_count == 1
    assert task.locked_at is None
    assert task.locked_by is None
    assert task.lease_token is None
    assert task.lease_expires_at is None


def test_move_tasks_between_queues_moves_only_matching_rows(db_session) -> None:
    """Queue moves filter by queue, status, and task type."""
    matching_task = ProcessingTask(
        task_type=TaskType.GENERATE_IMAGE.value,
        content_id=1,
        payload={},
        status=TaskStatus.PENDING.value,
        queue_name=TaskQueue.CONTENT.value,
    )
    wrong_type_task = ProcessingTask(
        task_type=TaskType.SUMMARIZE.value,
        content_id=2,
        payload={},
        status=TaskStatus.PENDING.value,
        queue_name=TaskQueue.CONTENT.value,
    )
    wrong_status_task = ProcessingTask(
        task_type=TaskType.GENERATE_IMAGE.value,
        content_id=3,
        payload={},
        status=TaskStatus.PROCESSING.value,
        queue_name=TaskQueue.CONTENT.value,
    )
    wrong_queue_task = ProcessingTask(
        task_type=TaskType.GENERATE_IMAGE.value,
        content_id=4,
        payload={},
        status=TaskStatus.PENDING.value,
        queue_name=TaskQueue.IMAGE.value,
    )
    db_session.add_all([matching_task, wrong_type_task, wrong_status_task, wrong_queue_task])
    db_session.commit()

    queue_control.move_tasks_between_queues(
        db_session,
        from_queue=TaskQueue.CONTENT.value,
        to_queue=TaskQueue.IMAGE.value,
        statuses=[TaskStatus.PENDING.value],
        task_type=TaskType.GENERATE_IMAGE.value,
        dry_run=False,
        force=True,
    )

    db_session.refresh(matching_task)
    db_session.refresh(wrong_type_task)
    db_session.refresh(wrong_status_task)
    db_session.refresh(wrong_queue_task)

    assert matching_task.queue_name == TaskQueue.IMAGE.value
    assert wrong_type_task.queue_name == TaskQueue.CONTENT.value
    assert wrong_status_task.queue_name == TaskQueue.CONTENT.value
    assert wrong_queue_task.queue_name == TaskQueue.IMAGE.value


def test_move_tasks_between_queues_dry_run_leaves_rows_unchanged(db_session) -> None:
    """Dry runs do not mutate queue assignments."""
    task = ProcessingTask(
        task_type=TaskType.GENERATE_IMAGE.value,
        content_id=1,
        payload={},
        status=TaskStatus.PENDING.value,
        queue_name=TaskQueue.CONTENT.value,
    )
    db_session.add(task)
    db_session.commit()

    queue_control.move_tasks_between_queues(
        db_session,
        from_queue=TaskQueue.CONTENT.value,
        to_queue=TaskQueue.IMAGE.value,
        statuses=[TaskStatus.PENDING.value],
        task_type=TaskType.GENERATE_IMAGE.value,
        dry_run=True,
        force=False,
    )

    db_session.refresh(task)
    assert task.queue_name == TaskQueue.CONTENT.value


def test_move_tasks_to_spec_queues_moves_misrouted_rows(db_session) -> None:
    """Spec-driven moves cover every queue split, not just media."""
    discussion_task = ProcessingTask(
        task_type=TaskType.FETCH_NEWS_ITEM_DISCUSSION.value,
        payload={"news_item_id": 10},
        status=TaskStatus.PENDING.value,
        queue_name=TaskQueue.CONTENT.value,
    )
    backfill_task = ProcessingTask(
        task_type=TaskType.BACKFILL_FEEDS.value,
        payload={"user_id": 1, "config_ids": [2], "count": 10},
        status=TaskStatus.PENDING.value,
        queue_name=TaskQueue.CONTENT.value,
    )
    audio_episode_task = ProcessingTask(
        task_type=TaskType.GENERATE_AUDIO_EPISODE.value,
        payload={"audio_episode_id": 3},
        status=TaskStatus.PROCESSING.value,
        queue_name=TaskQueue.MEDIA.value,
    )
    completed_misrouted_task = ProcessingTask(
        task_type=TaskType.FETCH_DISCUSSION.value,
        content_id=4,
        payload={},
        status=TaskStatus.COMPLETED.value,
        queue_name=TaskQueue.CONTENT.value,
    )
    already_correct_task = ProcessingTask(
        task_type=TaskType.SUMMARIZE.value,
        content_id=5,
        payload={},
        status=TaskStatus.PENDING.value,
        queue_name=TaskQueue.CONTENT.value,
    )
    db_session.add_all(
        [
            discussion_task,
            backfill_task,
            audio_episode_task,
            completed_misrouted_task,
            already_correct_task,
        ]
    )
    db_session.commit()

    queue_control.move_tasks_to_spec_queues(
        db_session,
        statuses=[TaskStatus.PENDING.value, TaskStatus.PROCESSING.value],
        task_type=None,
        limit=None,
        dry_run=False,
        force=True,
    )

    db_session.refresh(discussion_task)
    db_session.refresh(backfill_task)
    db_session.refresh(audio_episode_task)
    db_session.refresh(completed_misrouted_task)
    db_session.refresh(already_correct_task)

    assert discussion_task.queue_name == TaskQueue.DISCUSSION.value
    assert backfill_task.queue_name == TaskQueue.BACKFILL.value
    assert audio_episode_task.queue_name == TaskQueue.AUDIO_EPISODE.value
    assert completed_misrouted_task.queue_name == TaskQueue.CONTENT.value
    assert already_correct_task.queue_name == TaskQueue.CONTENT.value


def test_move_tasks_to_spec_queues_dry_run_leaves_rows_unchanged(db_session) -> None:
    """Spec-driven move previews do not mutate queue assignments."""
    task = ProcessingTask(
        task_type=TaskType.FETCH_DISCUSSION.value,
        content_id=1,
        payload={},
        status=TaskStatus.PENDING.value,
        queue_name=TaskQueue.CONTENT.value,
    )
    db_session.add(task)
    db_session.commit()

    queue_control.move_tasks_to_spec_queues(
        db_session,
        statuses=[TaskStatus.PENDING.value],
        task_type=None,
        limit=None,
        dry_run=True,
        force=False,
    )

    db_session.refresh(task)
    assert task.queue_name == TaskQueue.CONTENT.value
