"""Tests for analyze-url handler behavior."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import Mock

import pytest

from app.constants import SELF_SUBMISSION_SOURCE
from app.models.contracts import ContentStatus, ContentType
from app.models.db import (
    Content,
    ContentKnowledgeSave,
    ContentStatusEntry,
    UserIntegrationConnection,
    UserIntegrationSyncedItem,
)
from app.models.llm.content_analysis import (
    ContentAnalysisOutput,
    ContentAnalysisResult,
    InstructionLink,
    InstructionResult,
)
from app.pipeline.handlers.analyze_url import AnalyzeUrlHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.apple_podcasts import ApplePodcastResolution
from app.services.queue import TaskType
from app.services.x_api import XTweet, XTweetFetchResult, XTweetsPage


@pytest.fixture(autouse=True)
def _active_users(test_user, user_factory) -> None:
    second_user = user_factory()
    assert test_user.id == 1
    assert second_user.id == 2


def _metadata(value: object | None) -> dict[str, Any]:
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _build_context(db_session, queue_gateway: Mock) -> TaskContext:
    @contextmanager
    def _db_context():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    return TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test-worker",
        queue_gateway=queue_gateway,
        db_factory=_db_context,
    )


def _assert_process_content_enqueued(
    queue_gateway: Mock,
    *,
    db_session,
    content_id: int,
) -> None:
    queue_gateway.enqueue_many_in_session.assert_called_once()
    call = queue_gateway.enqueue_many_in_session.call_args
    assert call.args[0] is db_session
    requests = call.args[1]
    assert len(requests) == 1
    assert requests[0].task_type == TaskType.PROCESS_CONTENT
    assert requests[0].content_id == content_id


def _require_id(value: int | None) -> int:
    assert value is not None
    return value


def test_pattern_analysis_rolls_back_when_process_enqueue_fails(db_session) -> None:
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://www.youtube.com/watch?v=atomic-pattern",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={},
    )
    db_session.add(content)
    db_session.commit()
    content_id = _require_id(content.id)
    queue_gateway = Mock()
    queue_gateway.enqueue_many_in_session.side_effect = RuntimeError("queue unavailable")

    result = AnalyzeUrlHandler().handle(
        TaskEnvelope(
            id=10,
            task_type=TaskType.ANALYZE_URL,
            content_id=content_id,
            payload={"content_id": content_id},
        ),
        _build_context(db_session, queue_gateway),
    )

    assert result.success is False
    db_session.expire_all()
    persisted = db_session.query(Content).filter(Content.id == content_id).one()
    assert persisted.content_type == ContentType.UNKNOWN.value
    assert persisted.platform is None
    assert persisted.content_metadata == {}


def test_pattern_analysis_rebinds_type_collision_without_integrity_error(db_session) -> None:
    canonical = Content(
        content_type=ContentType.PODCAST.value,
        url="https://youtu.be/already-classified",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.COMPLETED.value,
        content_metadata={"content": "Existing article."},
    )
    duplicate = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://youtu.be/already-classified",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "submitted_by_user_id": 1,
            "submitted_via": "learning_deck",
        },
    )
    db_session.add_all([canonical, duplicate])
    db_session.flush()
    db_session.add(ContentKnowledgeSave(user_id=1, content_id=duplicate.id))
    db_session.commit()
    queue_gateway = Mock()

    result = AnalyzeUrlHandler().handle(
        TaskEnvelope(
            id=11,
            task_type=TaskType.ANALYZE_URL,
            content_id=duplicate.id,
            payload={"content_id": duplicate.id},
        ),
        _build_context(db_session, queue_gateway),
    )

    db_session.refresh(duplicate)
    assert result.success is True
    assert duplicate.content_type == ContentType.UNKNOWN.value
    assert duplicate.status == ContentStatus.SKIPPED.value
    assert _metadata(duplicate.content_metadata)["canonical_content_id"] == canonical.id
    queue_gateway.enqueue_many_in_session.assert_not_called()
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=1, content_id=duplicate.id)
        .one_or_none()
        is None
    )
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=1, content_id=canonical.id)
        .one_or_none()
        is not None
    )


def test_apple_podcast_pattern_resolution_uses_bounded_host_http(
    db_session,
    monkeypatch,
) -> None:
    apple_url = "https://podcasts.apple.com/us/podcast/example/id123?i=456"
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url=apple_url,
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "submitted_by_user_id": 1,
            "submitted_via": "share_action",
        },
    )
    db_session.add(content)
    db_session.commit()
    content_id = _require_id(content.id)
    host_http_service = Mock()

    def _resolve(url: str, *, feed_fetch) -> ApplePodcastResolution:  # noqa: ANN001
        assert url == apple_url
        assert feed_fetch == host_http_service.fetch
        return ApplePodcastResolution(
            feed_url="https://publisher.example/show.xml",
            episode_title="Host-fetched Episode",
            audio_url="https://cdn.example/episode.mp3",
        )

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.BoundedPublicHttpService",
        lambda: host_http_service,
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.resolve_apple_podcast_episode",
        _resolve,
    )
    queue_gateway = Mock()

    result = AnalyzeUrlHandler().handle(
        TaskEnvelope(
            id=13,
            task_type=TaskType.ANALYZE_URL,
            content_id=content_id,
            payload={"content_id": content_id},
        ),
        _build_context(db_session, queue_gateway),
    )

    assert result.success is True
    db_session.refresh(content)
    metadata = _metadata(content.content_metadata)
    assert metadata["feed_url"] == "https://publisher.example/show.xml"
    assert metadata["audio_url"] == "https://cdn.example/episode.mp3"
    _assert_process_content_enqueued(
        queue_gateway,
        db_session=db_session,
        content_id=content_id,
    )


def test_tweet_resolution_rolls_back_when_process_enqueue_fails(db_session) -> None:
    original_url = "https://x.com/someuser/status/123456789"
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url=original_url,
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "share_sheet",
            "platform_hint": "twitter",
            "tweet_snapshot": {
                "id": "123456789",
                "text": "Story link",
                "author_id": "42",
                "author_username": "willem",
                "author_name": "Willem",
                "created_at": "2026-03-27T21:56:00Z",
                "like_count": 12,
                "retweet_count": 3,
                "reply_count": 1,
                "conversation_id": "123456789",
                "external_urls": ["https://example.com/atomic-story"],
                "linked_tweet_ids": [],
                "referenced_tweet_types": [],
            },
            "tweet_snapshot_source": "share_sheet",
        },
    )
    db_session.add(content)
    db_session.commit()
    content_id = _require_id(content.id)
    queue_gateway = Mock()
    queue_gateway.enqueue_many_in_session.side_effect = RuntimeError("queue unavailable")

    result = AnalyzeUrlHandler().handle(
        TaskEnvelope(
            id=11,
            task_type=TaskType.ANALYZE_URL,
            content_id=content_id,
            payload={"content_id": content_id},
        ),
        _build_context(db_session, queue_gateway),
    )

    assert result.success is False
    db_session.expire_all()
    persisted = db_session.query(Content).filter(Content.id == content_id).one()
    assert persisted.url == original_url
    assert persisted.content_type == ContentType.UNKNOWN.value
    assert "tweet_processing_text" not in _metadata(persisted.content_metadata)


def test_instruction_fanout_rolls_back_when_process_enqueue_fails(
    db_session,
    monkeypatch,
) -> None:
    source_url = "https://example.com/atomic-instruction-source"
    child_url = "https://example.com/atomic-instruction-child"
    content = Content(
        content_type=ContentType.UNKNOWN.value,
        url=source_url,
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "share_sheet",
        },
    )
    db_session.add(content)
    db_session.commit()
    content_id = _require_id(content.id)

    analysis_output = ContentAnalysisOutput(
        analysis=ContentAnalysisResult(
            content_type="article",
            original_url=source_url,
            media_url=None,
            media_format=None,
            title=None,
            description=None,
            duration_seconds=None,
            platform="web",
        ),
        instruction=InstructionResult(
            text=None,
            links=[
                InstructionLink(
                    url=child_url,
                    title=None,
                    context=None,
                    platform=None,
                    source=None,
                )
            ],
        ),
    )
    llm_gateway = Mock()
    llm_gateway.analyze_url.return_value = analysis_output
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_llm_gateway",
        lambda: llm_gateway,
    )

    queue_gateway = Mock()
    queue_gateway.enqueue_many_in_session.side_effect = [
        [701],
        RuntimeError("queue unavailable"),
    ]
    monkeypatch.setattr(
        "app.services.instruction_links.get_task_queue_gateway",
        lambda: queue_gateway,
    )

    result = AnalyzeUrlHandler().handle(
        TaskEnvelope(
            id=12,
            task_type=TaskType.ANALYZE_URL,
            content_id=content_id,
            payload={
                "content_id": content_id,
                "instruction": "Find the linked source",
                "crawl_links": True,
            },
        ),
        _build_context(db_session, queue_gateway),
    )

    assert result.success is False
    db_session.expire_all()
    persisted = db_session.query(Content).filter(Content.id == content_id).one()
    assert persisted.content_type == ContentType.UNKNOWN.value
    assert persisted.platform is None
    assert db_session.query(Content).filter(Content.url == child_url).first() is None
    assert queue_gateway.enqueue_many_in_session.call_count == 2


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
    queue_gateway.enqueue_many_in_session.assert_not_called()
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
    queue_gateway.enqueue_many_in_session.assert_not_called()


def test_tweet_bookmark_failure_remains_visible_in_knowledge(
    db_session,
    monkeypatch,
) -> None:
    bookmark = Content(
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
    db_session.add(bookmark)
    db_session.commit()

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(success=False, error="Tweet lookup failed"),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )

    queue_gateway = Mock()
    result = AnalyzeUrlHandler().handle(
        TaskEnvelope(
            id=1002,
            task_type=TaskType.ANALYZE_URL,
            content_id=bookmark.id,
            payload={"content_id": bookmark.id},
        ),
        _build_context(db_session, queue_gateway=queue_gateway),
    )

    db_session.refresh(bookmark)
    assert result.success is False
    assert bookmark.status == ContentStatus.FAILED.value
    assert db_session.query(ContentKnowledgeSave).filter_by(user_id=1, content_id=bookmark.id).one()


def test_tweet_bookmark_does_not_restore_state_for_inactive_user(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    test_user.is_active = False
    bookmark = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://x.com/someuser/status/987654321",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": test_user.id,
            "submitted_via": "x_bookmarks",
            "platform_hint": "twitter",
        },
    )
    db_session.add(bookmark)
    db_session.commit()

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(success=False, error="Tweet lookup failed"),
    )

    def unexpected_token_lookup(*_args, **_kwargs):
        raise AssertionError("inactive user token must not be loaded")

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        unexpected_token_lookup,
    )
    result = AnalyzeUrlHandler().handle(
        TaskEnvelope(
            id=1003,
            task_type=TaskType.ANALYZE_URL,
            content_id=bookmark.id,
            payload={"content_id": bookmark.id},
        ),
        _build_context(db_session, queue_gateway=Mock()),
    )

    assert result.success is False
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=test_user.id, content_id=bookmark.id)
        .first()
        is None
    )


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
    connection = UserIntegrationConnection(
        user_id=1,
        provider="x",
        provider_user_id="42",
        is_active=True,
    )
    second_connection = UserIntegrationConnection(
        user_id=2,
        provider="x",
        provider_user_id="43",
        is_active=True,
    )
    db_session.add_all([existing_article, bookmark_shell, connection, second_connection])
    db_session.flush()
    synced_item = UserIntegrationSyncedItem(
        connection_id=connection.id,
        channel="bookmarks",
        external_item_id="123456789",
        content_id=_require_id(bookmark_shell.id),
        item_url="https://x.com/i/status/123456789",
    )
    second_synced_item = UserIntegrationSyncedItem(
        connection_id=second_connection.id,
        channel="bookmarks",
        external_item_id="123456789",
        content_id=_require_id(bookmark_shell.id),
        item_url="https://x.com/i/status/123456789",
    )
    db_session.add_all([synced_item, second_synced_item])
    db_session.commit()
    db_session.refresh(existing_article)
    db_session.refresh(bookmark_shell)
    db_session.refresh(synced_item)
    db_session.refresh(second_synced_item)

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
        content_id=_require_id(bookmark_shell.id),
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
    assert synced_item.content_id == existing_article.id
    assert second_synced_item.content_id == existing_article.id
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=1, content_id=bookmark_shell.id)
        .first()
        is None
    )
    assert db_session.query(ContentKnowledgeSave).filter_by(user_id=1).count() == 1
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=2, content_id=existing_article.id)
        .one()
    )
    assert db_session.query(ContentKnowledgeSave).filter_by(user_id=2).count() == 1
    assert db_session.query(Content).filter(Content.url == "https://example.com/story").count() == 1
    queue_gateway.enqueue_many_in_session.assert_not_called()


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
        content_id=_require_id(bookmark_shell.id),
        payload={"content_id": bookmark_shell.id},
    )

    result = AnalyzeUrlHandler().handle(task, context)

    db_session.refresh(bookmark_shell)
    metadata = _metadata(bookmark_shell.content_metadata)
    assert result.success is True
    assert bookmark_shell.url == "https://example.com/story"
    assert metadata["tweet_lookup_source"] == "bookmark_sync_snapshot"
    _assert_process_content_enqueued(
        queue_gateway,
        db_session=db_session,
        content_id=_require_id(bookmark_shell.id),
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
        "app.services.tweet_target_resolution.fetch_tweets_by_ids",
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
    _assert_process_content_enqueued(
        queue_gateway,
        db_session=db_session,
        content_id=_require_id(bookmark_shell.id),
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
    _assert_process_content_enqueued(
        queue_gateway,
        db_session=db_session,
        content_id=_require_id(bookmark_shell.id),
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
    _assert_process_content_enqueued(
        queue_gateway,
        db_session=db_session,
        content_id=_require_id(bookmark_shell.id),
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
        content_id=_require_id(content.id),
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
    _assert_process_content_enqueued(
        queue_gateway,
        db_session=db_session,
        content_id=_require_id(content.id),
    )


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
        "app.services.tweet_target_resolution.fetch_tweets_by_ids",
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
        "app.services.tweet_target_resolution.search_recent_tweets",
        lambda **_kwargs: XTweetsPage(tweets=[root_tweet, reply_tweet], next_token=None),
    )
    monkeypatch.setattr(
        "app.services.tweet_target_resolution.fetch_tweets_by_ids",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.tweet_target_resolution.fetch_user_tweets",
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
        "app.services.tweet_target_resolution.search_recent_tweets",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("thread lookup should be gated when there is no thread signal")
        ),
    )
    monkeypatch.setattr(
        "app.services.tweet_target_resolution.fetch_tweets_by_ids",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.tweet_target_resolution.fetch_user_tweets",
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


def test_tweet_share_rebinds_canonical_tweet_duplicate_without_integrity_error(
    db_session,
    monkeypatch,
) -> None:
    canonical = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://x.com/i/status/123456789",
        source_url="https://x.com/i/status/123456789",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.COMPLETED.value,
        content_metadata={"content": "Existing native X article."},
    )
    duplicate = Content(
        content_type=ContentType.UNKNOWN.value,
        url="https://x.com/willem/status/123456789?s=12",
        source_url="https://x.com/willem/status/123456789?s=12",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": SELF_SUBMISSION_SOURCE,
            "submitted_by_user_id": 1,
            "submitted_via": "share_action",
        },
    )
    db_session.add_all([canonical, duplicate])
    db_session.flush()
    db_session.add(ContentKnowledgeSave(user_id=1, content_id=duplicate.id))
    db_session.commit()

    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.fetch_tweet_by_id",
        lambda **_kwargs: XTweetFetchResult(
            success=True,
            tweet=XTweet(
                id="123456789",
                text="Native X article",
                author_username="willem",
                author_name="Willem",
                created_at="2026-08-15T16:00:00Z",
                article_title="Native X article",
                article_text="Existing native X article.",
                external_urls=[],
            ),
        ),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.analyze_url.get_x_user_access_token",
        lambda *_args, **_kwargs: None,
    )

    queue_gateway = Mock()
    task = TaskEnvelope(
        id=113,
        task_type=TaskType.ANALYZE_URL,
        content_id=duplicate.id,
        payload={"content_id": duplicate.id},
    )

    result = AnalyzeUrlHandler().handle(
        task,
        _build_context(db_session, queue_gateway=queue_gateway),
    )

    db_session.refresh(duplicate)
    metadata = _metadata(duplicate.content_metadata)
    assert result.success is True
    assert duplicate.content_type == ContentType.ARTICLE.value
    assert duplicate.status == ContentStatus.SKIPPED.value
    assert duplicate.url == "https://x.com/willem/status/123456789?s=12"
    assert metadata["canonical_content_id"] == canonical.id
    assert queue_gateway.enqueue_many_in_session.call_count == 0
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=1, content_id=duplicate.id)
        .one_or_none()
        is None
    )
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=1, content_id=canonical.id)
        .one_or_none()
        is not None
    )


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
        "app.services.tweet_target_resolution.fetch_user_tweets",
        lambda **_kwargs: XTweetsPage(tweets=[reply_tweet], next_token=None),
    )
    monkeypatch.setattr(
        "app.services.tweet_target_resolution.fetch_tweets_by_ids",
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
        "app.services.tweet_target_resolution.fetch_user_tweets",
        _fetch_user_tweets,
    )
    monkeypatch.setattr(
        "app.services.tweet_target_resolution.fetch_tweets_by_ids",
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
