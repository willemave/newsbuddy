"""Tests for retry policy handling in the sequential task processor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import UUID

from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.internal.queue import ClaimedTask, TaskTransition
from app.pipeline.sequential_task_processor import SequentialTaskProcessor
from app.pipeline.task_models import TaskResult

_LEASE_TOKEN = UUID("00000000-0000-0000-0000-000000000002")


def _claim(task_id: int, retry_count: int = 0) -> ClaimedTask:
    now = datetime.now(UTC).replace(tzinfo=None)
    return ClaimedTask.model_validate(
        {
            "id": task_id,
            "task_type": TaskType.SUMMARIZE.value,
            "content_id": None,
            "retry_count": retry_count,
            "payload": {},
            "status": TaskStatus.PROCESSING.value,
            "queue_name": TaskQueue.CONTENT.value,
            "created_at": now,
            "available_at": now,
            "started_at": now,
            "locked_at": now,
            "locked_by": "test-worker",
            "lease_token": _LEASE_TOKEN,
            "lease_expires_at": now + timedelta(minutes=5),
        }
    )


def _transition(
    claim: ClaimedTask,
    *,
    status: TaskStatus,
    retry_count: int,
) -> TaskTransition:
    return TaskTransition(
        task_type=claim.task_type,
        queue_name=claim.queue_name,
        content_id=claim.content_id,
        error_message="terminal failure" if status is TaskStatus.FAILED else "transient failure",
        status=status,
        retry_count=retry_count,
        retry_delay_seconds=60 if status is TaskStatus.PENDING else None,
        deferred=False,
        available_at=claim.available_at,
    )


def test_run_single_task_skips_retry_for_non_retryable_failure() -> None:
    processor = SequentialTaskProcessor()
    claim = _claim(task_id=99, retry_count=0)
    queue_service = Mock()
    queue_service.finalize_task.return_value = _transition(
        claim,
        status=TaskStatus.FAILED,
        retry_count=0,
    )
    queue_service.renew_lease.return_value = True
    processor.queue_service = queue_service
    processor.settings = Mock(queue=Mock(max_retries=3, worker_timeout_seconds=300))
    processor.__dict__["process_task"] = Mock(
        return_value=TaskResult.fail("terminal failure", retryable=False)
    )

    success = processor.run_single_task(claim)

    assert success is False
    queue_service.finalize_task.assert_called_once_with(
        claim,
        TaskResult.fail("terminal failure", retryable=False),
        max_retries=3,
    )


def test_run_single_task_retries_retryable_failure() -> None:
    processor = SequentialTaskProcessor()
    claim = _claim(task_id=100, retry_count=0)
    queue_service = Mock()
    queue_service.finalize_task.return_value = _transition(
        claim,
        status=TaskStatus.PENDING,
        retry_count=1,
    )
    queue_service.renew_lease.return_value = True
    processor.queue_service = queue_service
    processor.settings = Mock(queue=Mock(max_retries=3, worker_timeout_seconds=300))
    processor.__dict__["process_task"] = Mock(
        return_value=TaskResult.fail("transient failure", retryable=True)
    )

    success = processor.run_single_task(claim)

    assert success is False
    queue_service.finalize_task.assert_called_once_with(
        claim,
        TaskResult.fail("transient failure", retryable=True),
        max_retries=3,
    )
