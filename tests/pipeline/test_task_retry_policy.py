"""Tests for retry policy handling in the sequential task processor."""

from __future__ import annotations

from unittest.mock import Mock

from app.pipeline.sequential_task_processor import SequentialTaskProcessor
from app.pipeline.task_models import TaskResult
from app.services.queue import TaskType


def _task_data(task_id: int, retry_count: int = 0) -> dict[str, object]:
    return {
        "id": task_id,
        "task_type": TaskType.SUMMARIZE.value,
        "retry_count": retry_count,
        "payload": {},
    }


def test_run_single_task_skips_retry_for_non_retryable_failure() -> None:
    processor = SequentialTaskProcessor()
    queue_service = Mock()
    processor.queue_service = queue_service
    processor.settings = Mock(queue=Mock(max_retries=3))
    processor.__dict__["process_task"] = Mock(
        return_value=TaskResult.fail("terminal failure", retryable=False)
    )

    success = processor.run_single_task(_task_data(task_id=99, retry_count=0))

    assert success is False
    queue_service.complete_task.assert_called_once()
    queue_service.retry_task.assert_not_called()


def test_run_single_task_retries_retryable_failure() -> None:
    processor = SequentialTaskProcessor()
    queue_service = Mock()
    processor.queue_service = queue_service
    processor.settings = Mock(queue=Mock(max_retries=3))
    processor.__dict__["process_task"] = Mock(
        return_value=TaskResult.fail("transient failure", retryable=True)
    )

    success = processor.run_single_task(_task_data(task_id=100, retry_count=0))

    assert success is False
    queue_service.complete_task.assert_called_once()
    queue_service.retry_task.assert_called_once()
