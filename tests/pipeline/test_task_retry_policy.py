"""Tests for retry policy handling in the sequential task processor."""

from __future__ import annotations

from unittest.mock import Mock
from uuid import UUID

from app.pipeline.sequential_task_processor import SequentialTaskProcessor
from app.pipeline.task_models import TaskResult
from app.services.queue import TaskType

_LEASE_TOKEN = UUID("00000000-0000-0000-0000-000000000002")


def _task_data(task_id: int, retry_count: int = 0) -> dict[str, object]:
    return {
        "id": task_id,
        "task_type": TaskType.SUMMARIZE.value,
        "retry_count": retry_count,
        "payload": {},
        "locked_by": "test-worker",
        "lease_token": _LEASE_TOKEN,
    }


def test_run_single_task_skips_retry_for_non_retryable_failure() -> None:
    processor = SequentialTaskProcessor()
    queue_service = Mock()
    queue_service.finalize_task.return_value = {"status": "failed"}
    processor.queue_service = queue_service
    processor.settings = Mock(queue=Mock(max_retries=3))
    processor.__dict__["process_task"] = Mock(
        return_value=TaskResult.fail("terminal failure", retryable=False)
    )

    success = processor.run_single_task(_task_data(task_id=99, retry_count=0))

    assert success is False
    queue_service.finalize_task.assert_called_once_with(
        99,
        worker_id="test-worker",
        lease_token=_LEASE_TOKEN,
        success=False,
        error_message="terminal failure",
        retryable=False,
        current_retry_count=0,
        max_retries=3,
        retry_delay_seconds=None,
        deferred=False,
    )


def test_run_single_task_retries_retryable_failure() -> None:
    processor = SequentialTaskProcessor()
    queue_service = Mock()
    queue_service.finalize_task.return_value = {"status": "pending"}
    processor.queue_service = queue_service
    processor.settings = Mock(queue=Mock(max_retries=3))
    processor.__dict__["process_task"] = Mock(
        return_value=TaskResult.fail("transient failure", retryable=True)
    )

    success = processor.run_single_task(_task_data(task_id=100, retry_count=0))

    assert success is False
    queue_service.finalize_task.assert_called_once_with(
        100,
        worker_id="test-worker",
        lease_token=_LEASE_TOKEN,
        success=False,
        error_message="transient failure",
        retryable=True,
        current_retry_count=0,
        max_retries=3,
        retry_delay_seconds=60,
        deferred=False,
    )
