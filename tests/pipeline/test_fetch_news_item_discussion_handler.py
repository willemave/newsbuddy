"""Tests for news-item discussion task handler."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import Mock

from app.pipeline.handlers.fetch_news_item_discussion import FetchNewsItemDiscussionHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.news_item_discussions import NewsItemDiscussionRefreshResult
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


def test_handler_returns_non_retryable_when_news_item_id_missing(db_session) -> None:
    handler = FetchNewsItemDiscussionHandler()
    context = _build_context(db_session)
    task = TaskEnvelope(id=1, task_type=TaskType.FETCH_NEWS_ITEM_DISCUSSION, payload={})

    result = handler.handle(task, context)

    assert result.success is False
    assert result.retryable is False


def test_handler_returns_ok_on_success(db_session, monkeypatch) -> None:
    refresh_mock = Mock(
        return_value=NewsItemDiscussionRefreshResult(
            success=True,
            status="completed",
            refreshed=True,
            summarized=True,
        )
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.fetch_news_item_discussion.refresh_news_item_discussion",
        refresh_mock,
    )

    handler = FetchNewsItemDiscussionHandler()
    context = _build_context(db_session)
    task = TaskEnvelope(
        id=2,
        task_type=TaskType.FETCH_NEWS_ITEM_DISCUSSION,
        payload={"news_item_id": 123},
    )

    result = handler.handle(task, context)

    assert result.success is True
    refresh_mock.assert_called_once_with(
        db_session,
        news_item_id=123,
        summarizer=context.llm_service,
    )


def test_handler_propagates_retryability_on_failure(db_session, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.handlers.fetch_news_item_discussion.refresh_news_item_discussion",
        lambda _db, news_item_id, summarizer=None: NewsItemDiscussionRefreshResult(
            success=False,
            status="failed",
            error_message="timed out",
            retryable=True,
        ),
    )

    handler = FetchNewsItemDiscussionHandler()
    context = _build_context(db_session)
    task = TaskEnvelope(
        id=3,
        task_type=TaskType.FETCH_NEWS_ITEM_DISCUSSION,
        payload={"news_item_id": "123"},
    )

    result = handler.handle(task, context)

    assert result.success is False
    assert result.retryable is True
    assert result.error_message == "timed out"


def test_handler_completes_unsupported_discussion_task(db_session, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.handlers.fetch_news_item_discussion.refresh_news_item_discussion",
        lambda _db, news_item_id, summarizer=None: NewsItemDiscussionRefreshResult(
            success=False,
            status="unsupported",
            error_message="News item does not have a supported discussion source",
            retryable=False,
        ),
    )

    handler = FetchNewsItemDiscussionHandler()
    context = _build_context(db_session)
    task = TaskEnvelope(
        id=4,
        task_type=TaskType.FETCH_NEWS_ITEM_DISCUSSION,
        payload={"news_item_id": "123"},
    )

    result = handler.handle(task, context)

    assert result.success is True
