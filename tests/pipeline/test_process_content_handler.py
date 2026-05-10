"""Tests for process-content task handling."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import Mock

from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content
from app.pipeline.handlers.process_content import ProcessContentHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType


def _build_context(db_session) -> TaskContext:
    @contextmanager
    def _db_context():
        yield db_session

    return TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test-worker",
        db_factory=_db_context,
    )


def test_terminal_status_is_treated_as_completed_task(monkeypatch, db_session) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/failed",
        status=ContentStatus.FAILED.value,
        content_metadata={"processing_errors": [{"reason": "fetch failed"}]},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    monkeypatch.setattr(
        "app.pipeline.handlers.process_content.ContentWorker.process_content",
        lambda _self, _content_id, _worker_id: False,
    )

    handler = ProcessContentHandler()
    context = _build_context(db_session)
    task = TaskEnvelope(id=1, task_type=TaskType.PROCESS_CONTENT, content_id=content.id)

    result = handler.handle(task, context)

    assert result.success is True


def test_non_terminal_failure_remains_retryable(monkeypatch, db_session) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/processing",
        status=ContentStatus.PROCESSING.value,
        content_metadata={},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    monkeypatch.setattr(
        "app.pipeline.handlers.process_content.ContentWorker.process_content",
        lambda _self, _content_id, _worker_id: False,
    )

    handler = ProcessContentHandler()
    context = _build_context(db_session)
    task = TaskEnvelope(id=2, task_type=TaskType.PROCESS_CONTENT, content_id=content.id)

    result = handler.handle(task, context)

    assert result.success is False
    assert result.retryable is True
