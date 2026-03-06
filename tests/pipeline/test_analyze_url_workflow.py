"""Tests for analyze-url workflow orchestration."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.pipeline.workflows.analyze_url_workflow import AnalyzeUrlWorkflow
from app.services.queue import TaskType


def _build_context(db_session, queue_gateway: Mock) -> TaskContext:
    @contextmanager
    def _db_context():
        yield db_session

    return TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test-worker",
        queue_gateway=queue_gateway,
        db_factory=_db_context,
    )


def test_workflow_propagates_non_retryable_twitter_failures(db_session) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://x.com/i/status/987654321",
        source="self submission",
        status=ContentStatus.NEW.value,
        content_metadata={"submitted_by_user_id": 1},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    feed_flow = Mock()
    feed_flow.run.return_value = SimpleNamespace(handled=False, success=True)

    twitter_flow = Mock()
    twitter_flow.run.return_value = SimpleNamespace(
        handled=True,
        success=False,
        error_message="X app token missing",
        retryable=False,
    )

    analysis_flow = Mock()
    instruction_fanout = Mock()
    payload_cleaner = Mock()
    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)

    workflow = AnalyzeUrlWorkflow(
        feed_flow=feed_flow,
        twitter_flow=twitter_flow,
        analysis_flow=analysis_flow,
        instruction_fanout=instruction_fanout,
        payload_cleaner=payload_cleaner,
    )

    task = TaskEnvelope(
        id=42,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "crawl_links": True},
    )

    result = workflow.run(
        task=task,
        context=context,
        analysis_instruction=None,
        instruction=None,
        crawl_links=True,
        subscribe_to_feed=False,
    )

    assert result.success is False
    assert result.retryable is False
    assert result.error_message == "X app token missing"
    analysis_flow.run.assert_not_called()
    instruction_fanout.run.assert_not_called()
    payload_cleaner.run.assert_not_called()
    queue_gateway.enqueue.assert_not_called()


def test_workflow_uses_content_metadata_for_feed_subscription(db_session) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://example.com/article",
        source="self submission",
        status=ContentStatus.NEW.value,
        content_metadata={"subscribe_to_feed": True},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    feed_flow = Mock()
    feed_flow.run.return_value = SimpleNamespace(handled=True, success=True)

    twitter_flow = Mock()
    analysis_flow = Mock()
    instruction_fanout = Mock()
    payload_cleaner = Mock()
    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)

    workflow = AnalyzeUrlWorkflow(
        feed_flow=feed_flow,
        twitter_flow=twitter_flow,
        analysis_flow=analysis_flow,
        instruction_fanout=instruction_fanout,
        payload_cleaner=payload_cleaner,
    )

    task = TaskEnvelope(
        id=43,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id},
    )

    result = workflow.run(
        task=task,
        context=context,
        analysis_instruction=None,
        instruction=None,
        crawl_links=False,
        subscribe_to_feed=False,
    )

    assert result.success is True
    assert feed_flow.run.call_args.args[4] is True
    twitter_flow.run.assert_not_called()
    analysis_flow.run.assert_not_called()
    queue_gateway.enqueue.assert_not_called()
