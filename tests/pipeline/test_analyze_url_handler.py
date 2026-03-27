"""Tests for analyze-url handler behavior."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

from app.constants import DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT, SELF_SUBMISSION_SOURCE
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
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.backfill_feed_for_config",
        lambda request: SimpleNamespace(
            config_id=request.config_id,
            base_limit=1,
            target_limit=1 + request.count,
            scraped=2,
            saved=2,
            duplicates=0,
            errors=0,
        ),
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
    initial_download = content.content_metadata["feed_subscription"]["initial_download"]
    assert initial_download["ran"] is True
    assert initial_download["status"] == "completed"
    assert initial_download["requested_count"] == DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT
    assert initial_download["scraped"] == 2
    assert initial_download["saved"] == 2
    queue_gateway.enqueue.assert_not_called()

    config = (
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == 1, UserScraperConfig.feed_url == content.url)
        .first()
    )
    assert config is not None
    assert config.scraper_type == "atom"


def test_subscribe_to_feed_from_article_page_uses_detected_feed_url_and_page_title(
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://registerspill.thorstenball.com/p/joy-and-some-other-post",
        title="Register Spill",
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
        lambda _self, feed_url: None,
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_http_gateway",
        lambda: SimpleNamespace(fetch_content=lambda _url: ("<html></html>", {})),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.detect_feeds_from_html",
        lambda *_args, **_kwargs: {
            "detected_feed": {
                "url": "https://registerspill.thorstenball.com/feed",
                "type": "substack",
                "title": None,
                "format": "rss",
            }
        },
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.backfill_feed_for_config",
        lambda request: SimpleNamespace(
            config_id=request.config_id,
            base_limit=1,
            target_limit=1 + request.count,
            scraped=1,
            saved=1,
            duplicates=0,
            errors=0,
        ),
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=102,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    assert result.success is True
    assert content.status == ContentStatus.SKIPPED.value
    assert content.content_metadata["detected_feed"] == {
        "url": "https://registerspill.thorstenball.com/feed",
        "type": "substack",
        "title": None,
        "format": "rss",
    }
    assert content.content_metadata["feed_subscription"]["feed_url"] == (
        "https://registerspill.thorstenball.com/feed"
    )
    assert content.content_metadata["feed_subscription"]["feed_type"] == "substack"
    assert content.content_metadata["feed_subscription"]["created"] is True
    assert (
        content.content_metadata["feed_subscription"]["initial_download"]["status"]
        == "completed"
    )
    queue_gateway.enqueue.assert_not_called()

    config = (
        db_session.query(UserScraperConfig)
        .filter(
            UserScraperConfig.user_id == 1,
            UserScraperConfig.feed_url == "https://registerspill.thorstenball.com/feed",
        )
        .first()
    )
    assert config is not None
    assert config.scraper_type == "substack"
    assert config.display_name == "Register Spill"


def test_subscribe_to_feed_existing_subscription_skips_initial_download(
    db_session,
    monkeypatch,
) -> None:
    existing_config = UserScraperConfig(
        user_id=1,
        scraper_type="atom",
        display_name="Example Feed",
        config={"feed_url": "https://example.com/feed.xml", "limit": 1},
        feed_url="https://example.com/feed.xml",
        is_active=True,
    )
    db_session.add(existing_config)

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

    def _unexpected_backfill(_request):
        raise AssertionError("initial backfill should not run for existing subscriptions")

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.backfill_feed_for_config",
        _unexpected_backfill,
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=103,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    assert result.success is True
    assert content.content_metadata["feed_subscription"]["status"] == "already_exists"
    assert content.content_metadata["feed_subscription"]["created"] is False
    assert content.content_metadata["feed_subscription"]["config_id"] is None
    assert content.content_metadata["feed_subscription"]["initial_download"] == {
        "requested_count": DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
        "ran": False,
        "status": "skipped",
        "reason": "already_exists",
    }


def test_subscribe_to_feed_records_initial_download_failure(
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://example.com/failing-feed.xml",
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
            "title": "Failing Feed",
        },
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.FeedDetector.classify_feed_type",
        lambda _self, **_kwargs: SimpleNamespace(feed_type="atom"),
    )

    def _failing_backfill(_request):
        raise ValueError("scraper exploded")

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.backfill_feed_for_config",
        _failing_backfill,
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=104,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    assert result.success is True
    assert content.status == ContentStatus.SKIPPED.value
    assert content.content_metadata["feed_subscription"]["status"] == "created"
    assert content.content_metadata["feed_subscription"]["created"] is True
    initial_download = content.content_metadata["feed_subscription"]["initial_download"]
    assert initial_download["ran"] is True
    assert initial_download["status"] == "failed"
    assert initial_download["requested_count"] == DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT
    assert initial_download["error"] == "scraper exploded"
