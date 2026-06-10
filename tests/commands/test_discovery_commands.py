from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.commands import add_discovery_items, subscribe_discovery_suggestions
from app.models.api.discovery import DiscoveryAddItemRequest, DiscoverySubscribeRequest
from app.models.api.submissions import ContentSubmissionResponse
from app.models.contracts import ContentStatus, ContentType
from app.models.db import FeedDiscoveryRun, FeedDiscoverySuggestion, UserScraperConfig


def _create_run(db_session, user_id: int) -> FeedDiscoveryRun:
    run = FeedDiscoveryRun(
        user_id=user_id,
        status="completed",
        direction_summary="Test summary",
        seed_content_ids=[],
        created_at=datetime.now(UTC),
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def test_subscribe_discovery_suggestions_rejects_youtube_watch_url(
    db_session,
    test_user,
) -> None:
    run = _create_run(db_session, test_user.id)
    suggestion = FeedDiscoverySuggestion(
        run_id=run.id,
        user_id=test_user.id,
        suggestion_type="youtube",
        site_url="https://www.youtube.com/watch?v=abc123",
        feed_url="https://www.youtube.com/watch?v=abc123",
        title="Single YouTube video",
        status="new",
        config={"feed_url": "https://www.youtube.com/watch?v=abc123"},
    )
    db_session.add(suggestion)
    db_session.commit()
    db_session.refresh(suggestion)
    assert suggestion.id is not None

    response = subscribe_discovery_suggestions.execute(
        db_session,
        user_id=test_user.id,
        payload=DiscoverySubscribeRequest(suggestion_ids=[suggestion.id]),
    )

    assert response.subscribed == []
    assert response.skipped == [suggestion.id]
    assert response.errors == [
        {"id": str(suggestion.id), "error": "youtube_watch_url_requires_add_item"}
    ]
    assert db_session.query(UserScraperConfig).count() == 0


def test_add_discovery_items_uses_ingest_content_command(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    run = _create_run(db_session, test_user.id)
    suggestion = FeedDiscoverySuggestion(
        run_id=run.id,
        user_id=test_user.id,
        suggestion_type="podcast_rss",
        site_url="https://example.com",
        feed_url="https://example.com/feed.xml",
        item_url="https://example.com/episode-1",
        title="Example Episode",
        status="new",
        config={"feed_url": "https://example.com/feed.xml"},
    )
    db_session.add(suggestion)
    db_session.commit()
    db_session.refresh(suggestion)
    assert suggestion.id is not None

    calls = []

    def fake_ingest(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            response=ContentSubmissionResponse(
                content_id=123,
                content_type=ContentType.PODCAST,
                status=ContentStatus.NEW,
                already_exists=False,
                message="created",
            )
        )

    monkeypatch.setattr(add_discovery_items.ingest_content_command, "execute", fake_ingest)

    response = add_discovery_items.execute(
        db_session,
        current_user=test_user,
        payload=DiscoveryAddItemRequest(suggestion_ids=[suggestion.id]),
    )

    assert response.created == [123]
    assert response.skipped == []
    assert response.errors == []
    assert calls[0]["current_user"] == test_user
    assert str(calls[0]["payload"].url) == "https://example.com/episode-1"
