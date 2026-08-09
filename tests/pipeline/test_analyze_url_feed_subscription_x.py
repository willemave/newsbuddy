"""Tests for X share feed-subscription behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.constants import SELF_SUBMISSION_SOURCE
from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content
from app.pipeline.handlers.analyze_url import AnalyzeUrlHandler
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType
from app.services.x_api import XTweet, XTweetFetchResult
from tests.support.feed_subscription_test_helpers import (
    build_task_context,
    metadata_dict,
    stub_detector_feed,
    stub_feed_subscription_runtime,
)


@pytest.fixture(autouse=True)
def _use_fake_feed_sandbox(monkeypatch, test_user):
    assert test_user.id == 1
    stub_feed_subscription_runtime(monkeypatch)


def test_subscribe_to_feed_from_x_share_uses_tweet_article_url(
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://x.com/stratechery/status/2066460709920342279?s=12",
        title=None,
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "share_sheet",
            "subscribe_to_feed": True,
            "platform_hint": "twitter",
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    feed_url = "https://stratechery.com/feed/"
    stub_detector_feed(monkeypatch, feed_url=feed_url, feed_type="atom", title="Stratechery")
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(
            success=True,
            tweet=XTweet(
                id="2066460709920342279",
                text="Read this",
                author_id="u1",
                author_username="stratechery",
                author_name="Stratechery",
                created_at="2026-06-15T16:22:00Z",
                conversation_id="2066460709920342279",
                external_urls=["https://stratechery.com/2026/the-article/"],
            ),
        ),
    )
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )
    fetched_urls: list[str] = []

    def _fetch(url: str):
        fetched_urls.append(url)
        if url.startswith("https://x.com/"):
            return "<html><body>X shell</body></html>", {}
        return (
            '<html><head><link rel="alternate" type="application/rss+xml" '
            'href="https://stratechery.com/feed/" title="Stratechery"></head></html>',
            {},
        )

    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.get_http_gateway",
        lambda: SimpleNamespace(fetch_content=_fetch),
    )

    queue_gateway = Mock()
    context = build_task_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=115,
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
    assert metadata["feed_resolution_source"] == "x_tweet_external_url"
    assert metadata["feed_resolution_source_url"] == "https://stratechery.com/2026/the-article/"
    assert metadata["tweet_external_urls"] == ["https://stratechery.com/2026/the-article/"]
    assert feed_subscription["status"] == "created"
    assert fetched_urls == ["https://stratechery.com/2026/the-article/"]
    queue_gateway.enqueue.assert_not_called()


def test_subscribe_to_feed_from_x_share_prefers_feed_like_external_url(
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://x.com/example/status/123456789",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "share_sheet",
            "subscribe_to_feed": True,
            "platform_hint": "twitter",
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    feed_url = "https://example.com/rss.xml"
    stub_detector_feed(monkeypatch, feed_url=feed_url, feed_type="atom", title="Example")
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(
            success=True,
            tweet=XTweet(
                id="123456789",
                text="Two links",
                author_id="u1",
                author_username="example",
                author_name="Example",
                created_at="2026-06-15T16:22:00Z",
                conversation_id="123456789",
                external_urls=["https://example.com/story", feed_url],
            ),
        ),
    )
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.get_http_gateway",
        lambda: SimpleNamespace(
            fetch_content=lambda _url: (_ for _ in ()).throw(
                AssertionError("feed-like X links should be checked before page scraping")
            )
        ),
    )

    queue_gateway = Mock()
    context = build_task_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=116,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = metadata_dict(content.content_metadata)
    assert result.success is True
    assert metadata["detected_feed"]["url"] == feed_url
    assert metadata["feed_resolution_source"] == "x_tweet_external_url"
    assert metadata["feed_resolution_source_url"] == feed_url


def test_subscribe_to_feed_from_x_share_records_tweet_lookup_failure(
    db_session,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://x.com/example/status/123456789",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "share_sheet",
            "subscribe_to_feed": True,
            "platform_hint": "twitter",
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(
            success=False,
            error="X API unavailable",
        ),
    )
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.feed_subscription_resolution.get_http_gateway",
        lambda: SimpleNamespace(
            fetch_content=lambda _url: (_ for _ in ()).throw(
                AssertionError("X lookup failures should not fall back to scraping x.com")
            )
        ),
    )

    queue_gateway = Mock()
    context = build_task_context(db_session, queue_gateway=queue_gateway)
    task = TaskEnvelope(
        id=117,
        task_type=TaskType.ANALYZE_URL,
        content_id=content.id,
        payload={"content_id": content.id, "subscribe_to_feed": True},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(content)
    metadata = metadata_dict(content.content_metadata)
    feed_subscription = metadata_dict(metadata["feed_subscription"])
    tweet_enrichment = metadata_dict(metadata["tweet_enrichment"])
    assert result.success is True
    assert content.status == ContentStatus.SKIPPED.value
    assert feed_subscription["status"] == "tweet_lookup_failed"
    assert tweet_enrichment["status"] == "failed"
    assert tweet_enrichment["reason"] == "tweet_lookup_failed"
    assert tweet_enrichment["error"] == "X API unavailable"
    queue_gateway.enqueue.assert_not_called()
