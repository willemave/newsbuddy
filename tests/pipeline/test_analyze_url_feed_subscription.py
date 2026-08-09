"""Tests for analyze-url feed subscription behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.constants import DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT, SELF_SUBMISSION_SOURCE
from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content, ProcessingTask, UserScraperConfig
from app.pipeline.handlers.analyze_url import AnalyzeUrlHandler, FeedSubscriptionFlow
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType
from tests.support.feed_subscription_test_helpers import (
    build_task_context,
    metadata_dict,
    stub_detector_feed,
    stub_feed_subscription_runtime,
    stub_feed_validator,
)


@pytest.fixture(autouse=True)
def _use_fake_feed_sandbox(monkeypatch, test_user):
    assert test_user.id == 1
    stub_feed_subscription_runtime(monkeypatch)


def _assert_queued_backfill(db_session, *, config_id: int) -> ProcessingTask:
    task = (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.BACKFILL_FEEDS.value)
        .one()
    )
    payload = metadata_dict(task.payload)
    assert payload["user_id"] == 1
    assert payload["config_ids"] == [config_id]
    assert payload["count"] == DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT
    return task


def test_created_feed_subscription_requires_durable_backfill_task() -> None:
    with pytest.raises(RuntimeError, match="durable initial backfill task"):
        FeedSubscriptionFlow()._build_initial_feed_download(
            subscription_status="created",
            config_id=2,
            backfill_task_id=None,
            feed_type="atom",
        )


def test_reactivated_feed_subscription_projects_durable_backfill_task() -> None:
    initial_download = FeedSubscriptionFlow()._build_initial_feed_download(
        subscription_status="reactivated",
        config_id=2,
        backfill_task_id=11,
        feed_type="atom",
    )

    assert initial_download == {
        "requested_count": DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
        "ran": False,
        "status": "queued",
        "reason": "reactivated",
        "config_id": 2,
        "task_id": 11,
    }


def test_feed_subscription_flow_skips_resolution_for_inactive_user(
    db_session,
    test_user,
) -> None:
    test_user.is_active = False
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://example.com/inactive-feed",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": test_user.id,
            "subscribe_to_feed": True,
        },
    )
    db_session.add(content)
    db_session.commit()
    resolver = Mock()
    resolver.resolve.side_effect = AssertionError("inactive user must not run feed research")

    outcome = FeedSubscriptionFlow(resolver=resolver).run(
        db_session,
        content,
        dict(content.content_metadata),
        str(content.url),
        True,
    )

    db_session.refresh(content)
    assert outcome.handled is True
    assert outcome.success is True
    assert content.status == ContentStatus.SKIPPED.value
    assert metadata_dict(content.content_metadata)["feed_subscription"] == {
        "status": "inactive_user"
    }
    assert db_session.query(UserScraperConfig).count() == 0


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
    stub_feed_validator(monkeypatch)

    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.FeedDetector.validate_feed_url",
        lambda _self, feed_url: {
            "feed_url": feed_url,
            "feed_format": "rss",
            "title": "Example Feed",
        },
    )
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.FeedDetector.classify_feed_type",
        lambda _self, **_kwargs: SimpleNamespace(feed_type="atom"),
    )
    queue_gateway = Mock()
    context = build_task_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=101,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = metadata_dict(content.content_metadata)
    feed_subscription = metadata_dict(metadata["feed_subscription"])
    initial_download = metadata_dict(feed_subscription["initial_download"])
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
    assert initial_download["ran"] is False
    assert initial_download["status"] == "queued"
    assert initial_download["requested_count"] == DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT
    queue_gateway.enqueue.assert_not_called()

    config = (
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == 1, UserScraperConfig.feed_url == content.url)
        .first()
    )
    assert config is not None
    assert config.scraper_type == "atom"
    backfill_task = _assert_queued_backfill(db_session, config_id=int(config.id))
    assert feed_subscription["config_id"] == config.id
    assert feed_subscription["backfill_task_id"] == backfill_task.id
    assert initial_download["config_id"] == config.id
    assert initial_download["task_id"] == backfill_task.id


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
    stub_feed_validator(monkeypatch, title="Register Spill")

    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.FeedDetector.validate_feed_url",
        lambda _self, feed_url: None,
    )
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.get_http_gateway",
        lambda: SimpleNamespace(fetch_content=lambda _url: ("<html></html>", {})),
    )
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.detect_feeds_from_html",
        lambda *_args, **_kwargs: {
            "detected_feed": {
                "url": "https://registerspill.thorstenball.com/feed.xml",
                "type": "substack",
                "title": None,
                "format": "rss",
            }
        },
    )
    queue_gateway = Mock()
    context = build_task_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=102,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = metadata_dict(content.content_metadata)
    feed_subscription = metadata_dict(metadata["feed_subscription"])
    initial_download = metadata_dict(feed_subscription["initial_download"])
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
    assert initial_download["status"] == "queued"
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
    _assert_queued_backfill(db_session, config_id=int(config.id))


def test_subscribe_to_feed_explores_shared_page_links_until_feed_is_found(
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://links.example.com/today",
        title="Today's links",
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

    feed_url = "https://publisher.example.com/feed.xml"
    stub_detector_feed(monkeypatch, feed_url=feed_url, feed_type="atom", title="Publisher")

    pages = {
        "https://links.example.com/today": (
            "<html><body>"
            '<a href="https://other.example.com/post">Other</a>'
            '<a href="https://publisher.example.com/post">Publisher</a>'
            "</body></html>"
        ),
        "https://other.example.com/post": "<html><body>No feed here</body></html>",
        "https://publisher.example.com/post": (
            '<html><head><link rel="alternate" type="application/rss+xml" '
            'href="/feed.xml" title="Publisher"></head><body>Story</body></html>'
        ),
    }
    fetched_urls: list[str] = []

    def _fetch(url: str):
        fetched_urls.append(url)
        return pages[url], {}

    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.get_http_gateway",
        lambda: SimpleNamespace(fetch_content=_fetch),
    )

    queue_gateway = Mock()
    context = build_task_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=111,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = metadata_dict(content.content_metadata)
    feed_subscription = metadata_dict(metadata["feed_subscription"])
    assert result.success is True
    assert content.status == ContentStatus.SKIPPED.value
    assert metadata["detected_feed"]["url"] == feed_url
    assert metadata["feed_resolution_source_url"] == "https://publisher.example.com/post"
    assert feed_subscription["status"] == "created"
    assert fetched_urls == [
        "https://links.example.com/today",
        "https://other.example.com/post",
        "https://publisher.example.com/post",
    ]
    queue_gateway.enqueue.assert_not_called()


def test_subscribe_to_feed_from_apple_podcast_share_uses_publisher_rss(
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://podcasts.apple.com/us/podcast/example-show/id123?i=456",
        title="Example episode",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "share_sheet",
            "subscribe_to_feed": True,
            "platform_hint": "apple_podcasts",
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    feed_url = "https://feeds.example.com/example-show.xml"
    stub_detector_feed(monkeypatch, feed_url=feed_url, feed_type="podcast_rss")
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.resolve_apple_podcast_episode",
        lambda _url, **_kwargs: SimpleNamespace(
            feed_url=feed_url,
            episode_title="Example episode",
            audio_url=None,
        ),
    )
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.get_http_gateway",
        lambda: SimpleNamespace(
            fetch_content=lambda _url: (_ for _ in ()).throw(
                AssertionError("Apple podcast feed resolution should avoid page scraping")
            )
        ),
    )

    queue_gateway = Mock()
    context = build_task_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=112,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = metadata_dict(content.content_metadata)
    feed_subscription = metadata_dict(metadata["feed_subscription"])
    assert result.success is True
    assert metadata["detected_feed"]["url"] == feed_url
    assert metadata["detected_feed"]["type"] == "podcast_rss"
    assert metadata["feed_resolution_source"] == "apple_podcasts"
    assert feed_subscription["status"] == "created"
    config = (
        db_session.query(UserScraperConfig).filter(UserScraperConfig.feed_url == feed_url).one()
    )
    assert config.scraper_type == "podcast_rss"


def test_subscribe_to_feed_from_spotify_page_uses_linked_podcast_feed(
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://open.spotify.com/episode/abc123",
        title="Spotify episode",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "share_sheet",
            "subscribe_to_feed": True,
            "platform_hint": "spotify",
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    feed_url = "https://feeds.example.com/spotify-show.xml"
    stub_detector_feed(monkeypatch, feed_url=feed_url, feed_type="podcast_rss")
    monkeypatch.setattr(
        "app.services.feed_detection.resolve_apple_podcast_feed_url",
        lambda _url: feed_url,
    )
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.get_http_gateway",
        lambda: SimpleNamespace(
            fetch_content=lambda _url: (
                '<html><body><a href="https://podcasts.apple.com/us/podcast/show/id123">'
                "Apple Podcasts</a></body></html>",
                {},
            )
        ),
    )

    queue_gateway = Mock()
    context = build_task_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=113,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = metadata_dict(content.content_metadata)
    assert result.success is True
    assert metadata["detected_feed"]["url"] == feed_url
    assert metadata["detected_feed"]["type"] == "podcast_rss"
    assert metadata["feed_resolution_source_url"] == "https://open.spotify.com/episode/abc123"


def test_subscribe_to_feed_from_youtube_channel_creates_youtube_config(
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://www.youtube.com/@newsly",
        title="Newsly",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "share_sheet",
            "subscribe_to_feed": True,
            "platform_hint": "youtube",
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    queue_gateway = Mock()
    context = build_task_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=114,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = metadata_dict(content.content_metadata)
    feed_subscription = metadata_dict(metadata["feed_subscription"])
    assert result.success is True
    assert metadata["detected_feed"] == {
        "url": "https://www.youtube.com/@newsly",
        "type": "youtube",
        "title": "Newsly",
        "format": "youtube",
    }
    assert feed_subscription["status"] == "created"
    assert feed_subscription["initial_download"] == {
        "requested_count": DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
        "ran": False,
        "status": "skipped",
        "reason": "unsupported_scraper_type:youtube",
    }
    config = (
        db_session.query(UserScraperConfig)
        .filter(
            UserScraperConfig.user_id == 1,
            UserScraperConfig.scraper_type == "youtube",
            UserScraperConfig.feed_url == "https://www.youtube.com/@newsly",
        )
        .one()
    )
    assert config.display_name == "Newsly"


def test_subscribe_to_feed_from_youtube_video_uses_oembed_author_channel(
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://www.youtube.com/watch?v=abc123",
        title="Interview clip",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "share_sheet",
            "subscribe_to_feed": True,
            "platform_hint": "youtube",
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    def _fetch(url: str):
        assert url.startswith("https://www.youtube.com/oembed?")
        return (
            '{"author_url":"https://www.youtube.com/@creator","author_name":"Creator Channel"}',
            {},
        )

    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.get_http_gateway",
        lambda: SimpleNamespace(fetch_content=_fetch),
    )

    queue_gateway = Mock()
    context = build_task_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=1141,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = metadata_dict(content.content_metadata)
    assert result.success is True
    assert metadata["detected_feed"] == {
        "url": "https://www.youtube.com/@creator",
        "type": "youtube",
        "title": "Creator Channel",
        "format": "youtube",
    }
    assert metadata["feed_resolution_source"] == "youtube_oembed_author"
    config = (
        db_session.query(UserScraperConfig)
        .filter(
            UserScraperConfig.user_id == 1,
            UserScraperConfig.scraper_type == "youtube",
            UserScraperConfig.feed_url == "https://www.youtube.com/@creator",
        )
        .one()
    )
    assert config.display_name == "Creator Channel"


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
    stub_feed_validator(monkeypatch, title="Example Feed")

    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.FeedDetector.validate_feed_url",
        lambda _self, feed_url: {
            "feed_url": feed_url,
            "feed_format": "rss",
            "title": "Example Feed",
        },
    )
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.FeedDetector.classify_feed_type",
        lambda _self, **_kwargs: SimpleNamespace(feed_type="atom"),
    )

    queue_gateway = Mock()
    context = build_task_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=103,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = metadata_dict(content.content_metadata)
    feed_subscription = metadata_dict(metadata["feed_subscription"])
    assert result.success is True
    assert feed_subscription["status"] == "already_exists"
    assert feed_subscription["created"] is False
    assert feed_subscription["config_id"] == existing_config.id
    assert feed_subscription["initial_download"] == {
        "requested_count": DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
        "ran": False,
        "status": "skipped",
        "reason": "already_exists",
    }


def test_subscribe_to_feed_queue_failure_is_retryable_and_rolls_back(
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
    stub_feed_validator(monkeypatch, title="Failing Feed")

    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.FeedDetector.validate_feed_url",
        lambda _self, feed_url: {
            "feed_url": feed_url,
            "feed_format": "rss",
            "title": "Failing Feed",
        },
    )
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.FeedDetector.classify_feed_type",
        lambda _self, **_kwargs: SimpleNamespace(feed_type="atom"),
    )

    class _FailingQueueService:
        def enqueue_many_in_session(self, _db, _requests):
            raise RuntimeError("queue unavailable")

    monkeypatch.setattr(
        "app.commands.subscribe_feed.get_queue_service",
        lambda: _FailingQueueService(),
    )

    queue_gateway = Mock()
    context = build_task_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=104,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    assert result.success is False
    assert result.retryable is True
    assert result.error_message == "queue unavailable"

    db_session.rollback()
    db_session.refresh(content)
    assert content.status == ContentStatus.NEW.value
    assert (
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.feed_url == content.url)
        .count()
        == 0
    )
    assert (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.BACKFILL_FEEDS.value)
        .count()
        == 0
    )
