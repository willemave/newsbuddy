"""Tests for analyze-url handler behavior."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

from app.constants import SELF_SUBMISSION_SOURCE
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content, UserScraperConfig
from app.pipeline.handlers.analyze_url import AnalyzeUrlHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType
from app.services.x_api import XTweetFetchResult


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


def test_tweet_submission_missing_x_app_auth_fails_fast(
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://x.com/someuser/status/123456789",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "share_sheet",
            "platform_hint": "twitter",
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    def _missing_app_token(*, tweet_id: str, access_token: str | None = None) -> XTweetFetchResult:
        assert tweet_id == "123456789"
        assert access_token is None
        return XTweetFetchResult(
            success=False,
            error="X_APP_BEARER_TOKEN is required for app-authenticated X requests",
        )

    monkeypatch.setattr("app.pipeline.handlers.analyze_url.fetch_tweet_by_id", _missing_app_token)
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=100,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "crawl_links": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    assert result.success is False
    assert result.retryable is False
    assert content.status == ContentStatus.FAILED.value
    assert "X_APP_BEARER_TOKEN" in (content.error_message or "")
    assert content.content_metadata["tweet_enrichment"]["status"] == "failed"
    assert content.content_metadata["tweet_enrichment"]["reason"] == "x_app_auth_unavailable"
    queue_gateway.enqueue.assert_not_called()
    assert db_session.query(Content).count() == 1


def test_subscribe_to_feed_accepts_direct_feed_url(db_session, monkeypatch) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://example.com/feed.xml",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "share_sheet",
            "subscribe_to_feed": True,
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.FeedDetector.validate_feed_url",
        lambda _self, feed_url: {
            "feed_url": feed_url,
            "feed_format": "rss",
            "title": "Example Feed",
        },
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.FeedDetector.classify_feed_type",
        lambda _self, **_kwargs: SimpleNamespace(feed_type="atom"),
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=101,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    assert result.success is True
    assert content.status == ContentStatus.SKIPPED.value
    assert content.content_metadata["detected_feed"] == {
        "url": "https://example.com/feed.xml",
        "type": "atom",
        "title": "Example Feed",
        "format": "rss",
    }
    assert content.content_metadata["feed_subscription"]["status"] == "created"
    assert content.content_metadata["feed_subscription"]["feed_url"] == "https://example.com/feed.xml"
    assert content.content_metadata["feed_subscription"]["feed_type"] == "atom"
    assert content.content_metadata["feed_subscription"]["created"] is True
    queue_gateway.enqueue.assert_not_called()

    config = (
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == 1, UserScraperConfig.feed_url == content.url)
        .first()
    )
    assert config is not None
    assert config.scraper_type == "atom"
