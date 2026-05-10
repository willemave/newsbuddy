"""Tests for queue_control maintenance commands."""

from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.db import ProcessingTask
from scripts import queue_control


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
