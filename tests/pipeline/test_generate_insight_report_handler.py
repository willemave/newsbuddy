"""Tests for the generate-insight-report task handler."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

from app.pipeline.handlers.generate_insight_report import GenerateInsightReportHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.insight_report import DigDeeperArea, InsightReport
from app.services.queue import TaskType


def _report() -> InsightReport:
    return InsightReport(
        title="Test Insight Report",
        subtitle="what converges this week",
        intro="Short intro paragraph about the library.",
        themes=["theme a"],
        insights=["insight a"],
        learnings=["learning a"],
        curiosities=["curiosity a"],
        dig_deeper_areas=[DigDeeperArea(title="Dig A", prompt="Ask about A.")],
        referenced_knowledge_ids=[1, 2, 3],
    )


def _context(queue_service: Mock) -> TaskContext:
    @contextmanager
    def _db_context():
        yield object()

    return TaskContext(
        queue_service=queue_service,
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test-worker",
        db_factory=_db_context,
    )


def test_handler_requires_user_id():
    handler = GenerateInsightReportHandler()
    task = TaskEnvelope(
        id=1,
        task_type=TaskType.GENERATE_INSIGHT_REPORT,
        payload={},
    )
    result = handler.handle(task, _context(Mock()))
    assert result.success is False
    assert result.retryable is False


def test_handler_generates_persists_and_enqueues_image(monkeypatch):
    generated = _report()
    monkeypatch.setattr(
        "app.pipeline.handlers.generate_insight_report.generate_insight_report",
        lambda db, **kwargs: generated,
    )
    saved_content = SimpleNamespace(id=42)
    persist_mock = Mock(return_value=saved_content)
    monkeypatch.setattr(
        "app.pipeline.handlers.generate_insight_report.persist_insight_report",
        persist_mock,
    )

    queue_service = Mock()
    handler = GenerateInsightReportHandler()
    task = TaskEnvelope(
        id=1,
        task_type=TaskType.GENERATE_INSIGHT_REPORT,
        payload={"user_id": 7},
    )

    result = handler.handle(task, _context(queue_service))

    assert result.success is True
    persist_mock.assert_called_once()
    assert persist_mock.call_args.kwargs["user_id"] == 7
    assert persist_mock.call_args.kwargs["report"] is generated
    queue_service.enqueue.assert_called_once_with(
        TaskType.GENERATE_IMAGE,
        content_id=42,
        payload={"source": "insight_report"},
    )


def test_handler_skips_when_no_knowledge_saves(monkeypatch):
    def _raise(db, **kwargs):
        raise RuntimeError("No knowledge saves found for user_id=9")

    monkeypatch.setattr(
        "app.pipeline.handlers.generate_insight_report.generate_insight_report",
        _raise,
    )

    handler = GenerateInsightReportHandler()
    task = TaskEnvelope(
        id=1,
        task_type=TaskType.GENERATE_INSIGHT_REPORT,
        payload={"user_id": 9},
    )

    result = handler.handle(task, _context(Mock()))
    assert result.success is False
    assert result.retryable is False
    assert "No knowledge saves" in (result.error_message or "")
