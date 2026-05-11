"""Tests for analyze-url handler behavior."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

from app.constants import DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT, SELF_SUBMISSION_SOURCE
from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content, ContentKnowledgeSave, ContentStatusEntry, UserScraperConfig
from app.pipeline.handlers.analyze_url import AnalyzeUrlHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType
from app.services.x_api import XTweet, XTweetFetchResult, XTweetsPage


def _metadata(value: object | None) -> dict[str, Any]:
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


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

    def _missing_app_token(
        *,
        tweet_id: str,
        access_token: str | None = None,
        **_kwargs,
    ) -> XTweetFetchResult:
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
    metadata = _metadata(content.content_metadata)
    tweet_enrichment = _metadata(metadata["tweet_enrichment"])
    assert result.success is False
    assert result.retryable is False
    assert content.status == ContentStatus.FAILED.value
    assert "X_APP_BEARER_TOKEN" in (content.error_message or "")
    assert tweet_enrichment["status"] == "failed"
    assert tweet_enrichment["reason"] == "x_app_auth_unavailable"
    queue_gateway.enqueue.assert_not_called()
    assert db_session.query(Content).count() == 1


def test_tweet_submission_spend_cap_failure_is_non_retryable(
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

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(
            success=False,
            error="X API 403: SpendCapReached",
        ),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=1001,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "crawl_links": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = _metadata(content.content_metadata)
    tweet_enrichment = _metadata(metadata["tweet_enrichment"])
    assert result.success is False
    assert result.retryable is False
    assert content.status == ContentStatus.FAILED.value
    assert tweet_enrichment["status"] == "deferred"
    assert tweet_enrichment["reason"] == "x_spend_cap_reached"
    queue_gateway.enqueue.assert_not_called()


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
        "app.services.scraper_config_validation.FEED_VALIDATOR.validate_feed_url",
        lambda feed_url: {"feed_url": feed_url},
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
    monkeypatch.setattr(
        "app.services.scraper_config_validation.FEED_VALIDATOR.validate_feed_url",
        lambda feed_url: {
            "feed_url": feed_url,
            "feed_format": "rss",
            "title": "Register Spill",
        },
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
    metadata = _metadata(content.content_metadata)
    feed_subscription = _metadata(metadata["feed_subscription"])
    initial_download = _metadata(feed_subscription["initial_download"])
    assert result.success is True
    assert content.status == ContentStatus.SKIPPED.value
    assert metadata["detected_feed"] == {
        "url": "https://example.com/feed.xml",
        "type": "atom",
        "title": "Example Feed",
        "format": "rss",
    }
    assert feed_subscription["status"] == "created"
    assert feed_subscription["feed_url"] == "https://example.com/feed.xml"
    assert feed_subscription["feed_type"] == "atom"
    assert feed_subscription["created"] is True
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
        "app.services.scraper_config_validation.FEED_VALIDATOR.validate_feed_url",
        lambda feed_url: {"feed_url": feed_url},
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_http_gateway",
        lambda: SimpleNamespace(fetch_content=lambda _url: ("<html></html>", {})),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.detect_feeds_from_html",
        lambda *_args, **_kwargs: {
            "detected_feed": {
                "url": "https://registerspill.thorstenball.com/feed.xml",
                "type": "substack",
                "title": None,
                "format": "rss",
            }
        },
    )
    monkeypatch.setattr(
        "app.services.scraper_config_validation.FEED_VALIDATOR.validate_feed_url",
        lambda feed_url: {
            "feed_url": feed_url,
            "feed_format": "rss",
            "title": "Register Spill",
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
    metadata = _metadata(content.content_metadata)
    feed_subscription = _metadata(metadata["feed_subscription"])
    initial_download = _metadata(feed_subscription["initial_download"])
    assert result.success is True
    assert content.status == ContentStatus.SKIPPED.value
    assert metadata["detected_feed"] == {
        "url": "https://registerspill.thorstenball.com/feed.xml",
        "type": "substack",
        "title": None,
        "format": "rss",
    }
    assert feed_subscription["feed_url"] == ("https://registerspill.thorstenball.com/feed.xml")
    assert feed_subscription["feed_type"] == "substack"
    assert feed_subscription["created"] is True
    assert initial_download["status"] == "completed"
    queue_gateway.enqueue.assert_not_called()

    config = (
        db_session.query(UserScraperConfig)
        .filter(
            UserScraperConfig.user_id == 1,
            UserScraperConfig.feed_url == "https://registerspill.thorstenball.com/feed.xml",
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
    monkeypatch.setattr(
        "app.services.scraper_config_validation.FEED_VALIDATOR.validate_feed_url",
        lambda feed_url: {
            "feed_url": feed_url,
            "feed_format": "rss",
            "title": "Example Feed",
        },
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
    metadata = _metadata(content.content_metadata)
    feed_subscription = _metadata(metadata["feed_subscription"])
    assert result.success is True
    assert feed_subscription["status"] == "already_exists"
    assert feed_subscription["created"] is False
    assert feed_subscription["config_id"] is None
    assert feed_subscription["initial_download"] == {
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
        "app.services.scraper_config_validation.FEED_VALIDATOR.validate_feed_url",
        lambda feed_url: {"feed_url": feed_url},
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
    monkeypatch.setattr(
        "app.services.scraper_config_validation.FEED_VALIDATOR.validate_feed_url",
        lambda feed_url: {
            "feed_url": feed_url,
            "feed_format": "rss",
            "title": "Failing Feed",
        },
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
    metadata = _metadata(content.content_metadata)
    feed_subscription = _metadata(metadata["feed_subscription"])
    initial_download = _metadata(feed_subscription["initial_download"])
    assert result.success is True
    assert content.status == ContentStatus.SKIPPED.value
    assert feed_subscription["status"] == "created"
    assert feed_subscription["created"] is True
    assert initial_download["ran"] is True
    assert initial_download["status"] == "failed"
    assert initial_download["requested_count"] == DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT
    assert initial_download["error"] == "scraper exploded"


def test_tweet_bookmark_reuses_existing_article_when_primary_url_already_exists(
    db_session,
    monkeypatch,
) -> None:
    existing_article = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/story",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
        },
    )
    bookmark_shell = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://x.com/someuser/status/123456789",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "x_bookmarks",
            "platform_hint": "twitter",
        },
    )
    db_session.add(existing_article)
    db_session.add(bookmark_shell)
    db_session.commit()
    db_session.refresh(existing_article)
    db_session.refresh(bookmark_shell)

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(
            success=True,
            tweet=XTweet(
                id="123456789",
                text="Story link https://t.co/story",
                author_username="willem",
                author_name="Willem",
                created_at="2026-03-27T21:56:00Z",
                like_count=12,
                retweet_count=3,
                reply_count=1,
                external_urls=["https://example.com/story"],
            ),
        ),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )
    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=105,
        task_type=TaskType.ANALYZE_URL,
        content_id=bookmark_shell.id,
        payload={"content_id": bookmark_shell.id},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(existing_article)
    db_session.refresh(bookmark_shell)
    status_row = (
        db_session.query(ContentStatusEntry)
        .filter(
            ContentStatusEntry.content_id == existing_article.id,
            ContentStatusEntry.user_id == 1,
        )
        .first()
    )
    knowledge_row = (
        db_session.query(ContentKnowledgeSave)
        .filter(
            ContentKnowledgeSave.content_id == existing_article.id,
            ContentKnowledgeSave.user_id == 1,
        )
        .first()
    )

    assert result.success is True
    assert bookmark_shell.status == ContentStatus.SKIPPED.value
    assert bookmark_shell.error_message == "Canonical URL conflicts with existing content"
    assert _metadata(bookmark_shell.content_metadata)["canonical_content_id"] == existing_article.id
    assert status_row is None
    assert knowledge_row is not None
    assert db_session.query(Content).filter(Content.url == "https://example.com/story").count() == 1
    queue_gateway.enqueue.assert_not_called()


def test_tweet_bookmark_uses_sync_snapshot_before_fetching_x_again(
    db_session,
    monkeypatch,
) -> None:
    bookmark_shell = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://x.com/someuser/status/123456789",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "x_bookmarks",
            "platform_hint": "twitter",
            "tweet_snapshot": {
                "id": "123456789",
                "text": "Story link https://t.co/story",
                "author_id": "42",
                "author_username": "willem",
                "author_name": "Willem",
                "created_at": "2026-03-27T21:56:00Z",
                "like_count": 12,
                "retweet_count": 3,
                "reply_count": 1,
                "conversation_id": "123456789",
                "external_urls": ["https://example.com/story"],
                "linked_tweet_ids": [],
                "referenced_tweet_types": [],
            },
            "tweet_snapshot_source": "x_bookmarks_sync",
        },
    )
    db_session.add(bookmark_shell)
    db_session.commit()
    db_session.refresh(bookmark_shell)

    def _unexpected_fetch(**_kwargs):
        raise AssertionError("tweet fetch should use bookmark snapshot")

    monkeypatch.setattr("app.pipeline.handlers.analyze_url.fetch_tweet_by_id", _unexpected_fetch)
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=1051,
        task_type=TaskType.ANALYZE_URL,
        content_id=bookmark_shell.id,
        payload={"content_id": bookmark_shell.id},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(bookmark_shell)
    metadata = _metadata(bookmark_shell.content_metadata)
    assert result.success is True
    assert bookmark_shell.url == "https://example.com/story"
    assert metadata["tweet_lookup_source"] == "bookmark_sync_snapshot"
    queue_gateway.enqueue.assert_called_once_with(
        TaskType.PROCESS_CONTENT,
        content_id=bookmark_shell.id,
    )


def test_tweet_bookmark_uses_included_snapshot_for_linked_tweet_resolution(
    db_session,
    monkeypatch,
) -> None:
    bookmark_shell = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://x.com/someuser/status/123456789",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "x_bookmarks",
            "platform_hint": "twitter",
            "tweet_snapshot": {
                "id": "123456789",
                "text": "Root tweet body",
                "author_id": "42",
                "author_username": "willem",
                "author_name": "Willem",
                "created_at": "2026-03-27T21:56:00Z",
                "like_count": 12,
                "retweet_count": 3,
                "reply_count": 1,
                "conversation_id": "123456789",
                "external_urls": [],
                "linked_tweet_ids": ["987654321"],
                "referenced_tweet_types": ["quoted"],
            },
            "tweet_snapshot_included": {
                "987654321": {
                    "id": "987654321",
                    "text": "Linked tweet with url",
                    "author_id": "42",
                    "author_username": "willem",
                    "author_name": "Willem",
                    "created_at": "2026-03-27T21:57:00Z",
                    "like_count": 4,
                    "retweet_count": 1,
                    "reply_count": 0,
                    "conversation_id": "123456789",
                    "external_urls": ["https://example.com/story"],
                    "linked_tweet_ids": [],
                    "referenced_tweet_types": [],
                }
            },
            "tweet_snapshot_source": "x_bookmarks_sync",
        },
    )
    db_session.add(bookmark_shell)
    db_session.commit()
    db_session.refresh(bookmark_shell)

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweet_by_id",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("root tweet should come from metadata snapshot")
        ),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweets_by_ids",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("linked tweet should come from included snapshot")
        ),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=1052,
        task_type=TaskType.ANALYZE_URL,
        content_id=bookmark_shell.id,
        payload={"content_id": bookmark_shell.id},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(bookmark_shell)
    metadata = _metadata(bookmark_shell.content_metadata)
    assert result.success is True
    assert bookmark_shell.url == "https://example.com/story"
    assert metadata["tweet_resolution_source"] == "linked_tweet"
    assert metadata["tweet_lookup_source"] == "bookmark_sync_snapshot"
    queue_gateway.enqueue.assert_called_once_with(
        TaskType.PROCESS_CONTENT,
        content_id=bookmark_shell.id,
    )


def test_tweet_bookmark_records_native_x_article_metadata(
    db_session,
    monkeypatch,
) -> None:
    bookmark_shell = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://x.com/someuser/status/123456789",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "x_bookmarks",
            "platform_hint": "twitter",
        },
    )
    db_session.add(bookmark_shell)
    db_session.commit()
    db_session.refresh(bookmark_shell)

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(
            success=True,
            tweet=XTweet(
                id="123456789",
                text="Short teaser for the native article",
                author_username="willem",
                author_name="Willem",
                created_at="2026-03-27T21:56:00Z",
                like_count=12,
                retweet_count=3,
                reply_count=1,
                article_title="Native X Article",
                article_text="This is the full native X article body.",
                external_urls=[],
            ),
        ),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=106,
        task_type=TaskType.ANALYZE_URL,
        content_id=bookmark_shell.id,
        payload={"content_id": bookmark_shell.id},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(bookmark_shell)
    metadata = _metadata(bookmark_shell.content_metadata)
    status_row = (
        db_session.query(ContentStatusEntry)
        .filter(
            ContentStatusEntry.content_id == bookmark_shell.id,
            ContentStatusEntry.user_id == 1,
        )
        .first()
    )
    knowledge_row = (
        db_session.query(ContentKnowledgeSave)
        .filter(
            ContentKnowledgeSave.content_id == bookmark_shell.id,
            ContentKnowledgeSave.user_id == 1,
        )
        .first()
    )

    assert result.success is True
    assert bookmark_shell.content_type == ContentType.ARTICLE.value
    assert bookmark_shell.url == "https://x.com/i/status/123456789"
    assert bookmark_shell.title == "Native X Article"
    assert metadata["tweet_article_title"] == "Native X Article"
    assert metadata["tweet_article_text"] == "This is the full native X article body."
    assert metadata["tweet_processing_text"] == (
        "Native X Article\n\nThis is the full native X article body."
    )
    assert "tweet_only" not in metadata
    assert status_row is None
    assert knowledge_row is not None
    queue_gateway.enqueue.assert_called_once_with(
        TaskType.PROCESS_CONTENT,
        content_id=bookmark_shell.id,
    )


def test_tweet_bookmark_resolves_linked_podcast_as_long_form_podcast(
    db_session,
    monkeypatch,
) -> None:
    bookmark_shell = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://x.com/someuser/status/123456789",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "x_bookmarks",
            "platform_hint": "twitter",
        },
    )
    db_session.add(bookmark_shell)
    db_session.commit()
    db_session.refresh(bookmark_shell)
    metadata = _metadata(bookmark_shell.content_metadata)

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(
            success=True,
            tweet=XTweet(
                id="123456789",
                text="Listen here",
                author_username="willem",
                author_name="Willem",
                created_at="2026-03-27T21:56:00Z",
                like_count=12,
                retweet_count=3,
                reply_count=1,
                external_urls=["https://podcasts.apple.com/us/podcast/example-show/id123?i=456"],
            ),
        ),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=107,
        task_type=TaskType.ANALYZE_URL,
        content_id=bookmark_shell.id,
        payload={"content_id": bookmark_shell.id},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(bookmark_shell)
    metadata = _metadata(bookmark_shell.content_metadata)
    status_row = (
        db_session.query(ContentStatusEntry)
        .filter(
            ContentStatusEntry.content_id == bookmark_shell.id,
            ContentStatusEntry.user_id == 1,
        )
        .first()
    )
    knowledge_row = (
        db_session.query(ContentKnowledgeSave)
        .filter(
            ContentKnowledgeSave.content_id == bookmark_shell.id,
            ContentKnowledgeSave.user_id == 1,
        )
        .first()
    )

    assert result.success is True
    assert bookmark_shell.content_type == ContentType.PODCAST.value
    assert bookmark_shell.platform == "apple_podcasts"
    assert bookmark_shell.url == "https://podcasts.apple.com/us/podcast/example-show/id123?i=456"
    assert metadata["tweet_resolution_source"] == "root_tweet"
    assert status_row is None
    assert knowledge_row is not None
    queue_gateway.enqueue.assert_called_once_with(
        TaskType.PROCESS_CONTENT,
        content_id=bookmark_shell.id,
    )


def test_tweet_share_uses_root_article_url_without_fanout(db_session, monkeypatch) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
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

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(
            success=True,
            tweet=XTweet(
                id="123456789",
                text="Root tweet body",
                author_id="u1",
                author_username="willem",
                author_name="Willem",
                created_at="2026-03-29T10:00:00Z",
                conversation_id="123456789",
                external_urls=["https://example.com/root-story", "https://example.com/extra-story"],
            ),
        ),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=107,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = _metadata(content.content_metadata)

    assert result.success is True
    assert content.url == "https://example.com/root-story"
    assert metadata["tweet_text"] == "Root tweet body"
    assert metadata["tweet_resolution_source"] == "root_tweet"
    assert metadata["tweet_resolution_tweet_id"] == "123456789"
    assert metadata["tweet_thread_lookup_status"] == "not_needed"
    assert db_session.query(Content).count() == 1
    queue_gateway.enqueue.assert_called_once_with(TaskType.PROCESS_CONTENT, content_id=content.id)


def test_tweet_share_resolves_article_from_linked_tweet(db_session, monkeypatch) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
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

    root_tweet = XTweet(
        id="123456789",
        text="Root tweet body",
        author_id="u1",
        author_username="willem",
        author_name="Willem",
        created_at="2026-03-29T10:00:00Z",
        conversation_id="123456789",
        linked_tweet_ids=["987654321"],
        external_urls=[],
    )
    linked_tweet = XTweet(
        id="987654321",
        text="Quoted post",
        author_id="u2",
        author_username="alice",
        author_name="Alice",
        created_at="2026-03-29T10:01:00Z",
        conversation_id="987654321",
        external_urls=["https://example.com/linked-story"],
    )

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(success=True, tweet=root_tweet),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweets_by_ids",
        lambda **_kwargs: [linked_tweet],
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=108,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = _metadata(content.content_metadata)

    assert result.success is True
    assert content.url == "https://example.com/linked-story"
    assert metadata["tweet_text"] == "Root tweet body"
    assert metadata["tweet_linked_tweet_ids"] == ["987654321"]
    assert metadata["tweet_resolution_source"] == "linked_tweet"
    assert metadata["tweet_resolution_tweet_id"] == "987654321"
    assert metadata["tweet_thread_lookup_status"] == "not_needed"


def test_tweet_share_resolves_article_from_same_author_thread_reply(
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
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

    root_tweet = XTweet(
        id="123456789",
        text="Thread root",
        author_id="u1",
        author_username="willem",
        author_name="Willem",
        created_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        conversation_id="123456789",
        external_urls=[],
    )
    reply_tweet = XTweet(
        id="123456790",
        text="Here is the link",
        author_id="u1",
        author_username="willem",
        author_name="Willem",
        created_at=(datetime.now(UTC) - timedelta(hours=1, minutes=59))
        .isoformat()
        .replace("+00:00", "Z"),
        conversation_id="123456789",
        external_urls=["https://example.com/thread-story"],
    )

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(success=True, tweet=root_tweet),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.search_recent_tweets",
        lambda **_kwargs: XTweetsPage(tweets=[root_tweet, reply_tweet], next_token=None),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweets_by_ids",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_user_tweets",
        lambda **_kwargs: XTweetsPage(tweets=[root_tweet], next_token=None),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=109,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = _metadata(content.content_metadata)

    assert result.success is True
    assert content.url == "https://example.com/thread-story"
    assert metadata["tweet_resolution_source"] == "thread_reply"
    assert metadata["tweet_resolution_tweet_id"] == "123456790"
    assert metadata["tweet_thread_lookup_status"] == "found"
    assert metadata["tweet_thread_text"] == "Thread root\n\nHere is the link"


def test_tweet_share_falls_back_to_tweet_only_when_no_article_found(
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
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

    root_tweet = XTweet(
        id="123456789",
        text="Tweet only",
        author_id="u1",
        author_username="willem",
        author_name="Willem",
        created_at="2026-03-29T10:00:00Z",
        conversation_id="123456789",
        external_urls=[],
    )

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(success=True, tweet=root_tweet),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.search_recent_tweets",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("thread lookup should be gated when there is no thread signal")
        ),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweets_by_ids",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_user_tweets",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("timeline fanout should be gated when there is no thread signal")
        ),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=110,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = _metadata(content.content_metadata)

    assert result.success is True
    assert content.url == "https://x.com/i/status/123456789"
    assert metadata["tweet_resolution_source"] == "tweet_only"
    assert metadata["tweet_thread_lookup_status"] == "not_attempted"
    assert metadata["tweet_only"] is True


def test_tweet_share_uses_user_timeline_for_older_threads(db_session, monkeypatch) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
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

    root_tweet = XTweet(
        id="123456789",
        text="Old thread root",
        author_id="u1",
        author_username="willem",
        author_name="Willem",
        created_at="2026-03-01T10:00:00Z",
        conversation_id="123456789",
        external_urls=[],
    )
    reply_tweet = XTweet(
        id="123456790",
        text="Old thread reply",
        author_id="u1",
        author_username="willem",
        author_name="Willem",
        created_at="2026-03-01T10:02:00Z",
        conversation_id="123456789",
        external_urls=["https://example.com/old-thread-story"],
    )

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(success=True, tweet=root_tweet),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_user_tweets",
        lambda **_kwargs: XTweetsPage(tweets=[reply_tweet], next_token=None),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweets_by_ids",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=111,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = _metadata(content.content_metadata)

    assert result.success is True
    assert content.url == "https://example.com/old-thread-story"
    assert metadata["tweet_resolution_source"] == "thread_reply"
    assert metadata["tweet_resolution_tweet_id"] == "123456790"
    assert metadata["tweet_thread_lookup_status"] == "found"


def test_tweet_share_records_capped_thread_lookup_and_degrades_gracefully(
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
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

    root_tweet = XTweet(
        id="123456789",
        text="Old thread root",
        author_id="u1",
        author_username="willem",
        author_name="Willem",
        created_at="2026-03-01T10:00:00Z",
        conversation_id="123456789",
        external_urls=[],
    )
    call_counter = {"count": 0}

    def _fetch_user_tweets(**_kwargs):
        call_counter["count"] += 1
        return XTweetsPage(tweets=[], next_token="next")

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(success=True, tweet=root_tweet),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_user_tweets",
        _fetch_user_tweets,
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweets_by_ids",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )

    queue_gateway = Mock()
    context = _build_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=112,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = _metadata(content.content_metadata)

    assert result.success is True
    assert call_counter["count"] == 10
    assert content.url == "https://x.com/i/status/123456789"
    assert metadata["tweet_resolution_source"] == "tweet_only"
    assert metadata["tweet_thread_lookup_status"] == "capped"
    assert metadata["tweet_only"] is True
