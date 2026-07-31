"""Tests for task dispatcher and envelope models."""

from unittest.mock import Mock

from app.pipeline.dispatcher import TaskDispatcher
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.queue import TaskType


def test_task_envelope_parses_handler_data() -> None:
    task_data = {
        "id": 10,
        "task_type": "scrape",
        "retry_count": 0,
        "payload": {"sources": ["all"]},
    }

    envelope = TaskEnvelope.model_validate(task_data)

    assert envelope.id == 10
    assert envelope.task_type == TaskType.SCRAPE
    assert envelope.payload["sources"] == ["all"]


def test_task_envelope_normalizes_payload_none() -> None:
    task_data = {
        "id": 11,
        "task_type": "scrape",
        "retry_count": 0,
        "payload": None,
    }

    envelope = TaskEnvelope.model_validate(task_data)

    assert envelope.payload == {}


def test_dispatcher_returns_error_for_unknown_handler() -> None:
    dispatcher = TaskDispatcher([])
    task = TaskEnvelope(id=1, task_type=TaskType.SCRAPE, retry_count=0, payload={})
    context = TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test",
    )

    result = dispatcher.dispatch(task, context)

    assert result.success is False
    assert result.error_message == "Unknown task type: scrape"


def test_dispatcher_calls_handler() -> None:
    class DummyHandler:
        task_type = TaskType.SCRAPE

        def handle(self, task, context):
            return TaskResult.ok()

    dispatcher = TaskDispatcher([DummyHandler()])
    task = TaskEnvelope(id=2, task_type=TaskType.SCRAPE, retry_count=0, payload={})
    context = TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test",
    )

    result = dispatcher.dispatch(task, context)

    assert result.success is True
