from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.commands import (
    add_discovery_items,
    dismiss_discovery_suggestions,
    subscribe_discovery_suggestions,
)
from app.models.api.discovery import (
    DiscoveryAddItemRequest,
    DiscoveryDismissRequest,
    DiscoverySubscribeRequest,
)
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


def test_dismiss_discovery_suggestions_only_updates_owned_rows(
    db_session,
    test_user,
    user_factory,
) -> None:
    other_user = user_factory()
    owned_run = _create_run(db_session, test_user.id)
    other_run = _create_run(db_session, other_user.id)
    owned_run_id = owned_run.id
    other_run_id = other_run.id
    assert owned_run_id is not None
    assert other_run_id is not None
    owned = FeedDiscoverySuggestion(
        run_id=owned_run_id,
        user_id=test_user.id,
        suggestion_type="atom",
        site_url="https://example.com",
        feed_url="https://example.com/feed.xml",
        title="Example Feed",
        status="new",
        config={"feed_url": "https://example.com/feed.xml"},
    )
    foreign = FeedDiscoverySuggestion(
        run_id=other_run_id,
        user_id=other_user.id,
        suggestion_type="atom",
        site_url="https://other.example.com",
        feed_url="https://other.example.com/feed.xml",
        title="Other Feed",
        status="new",
        config={"feed_url": "https://other.example.com/feed.xml"},
    )
    db_session.add_all([owned, foreign])
    db_session.commit()
    db_session.refresh(owned)
    db_session.refresh(foreign)
    assert owned.id is not None
    assert foreign.id is not None

    response = dismiss_discovery_suggestions.execute(
        db_session,
        user_id=test_user.id,
        payload=DiscoveryDismissRequest(suggestion_ids=[owned.id, foreign.id]),
    )

    assert response.dismissed == [owned.id]
    db_session.refresh(owned)
    db_session.refresh(foreign)
    assert owned.status == "dismissed"
    assert foreign.status == "new"


def test_clear_discovery_suggestions_updates_only_non_dismissed_owned_rows(
    db_session,
    test_user,
    user_factory,
) -> None:
    other_user = user_factory()
    owned_run = _create_run(db_session, test_user.id)
    other_run = _create_run(db_session, other_user.id)
    owned_run_id = owned_run.id
    other_run_id = other_run.id
    assert owned_run_id is not None
    assert other_run_id is not None

    def suggestion(
        *, run_id: int, user_id: int, suffix: str, status: str
    ) -> FeedDiscoverySuggestion:
        return FeedDiscoverySuggestion(
            run_id=run_id,
            user_id=user_id,
            suggestion_type="atom",
            site_url=f"https://{suffix}.example.com",
            feed_url=f"https://{suffix}.example.com/feed.xml",
            title=f"{suffix.title()} Feed",
            status=status,
            config={"feed_url": f"https://{suffix}.example.com/feed.xml"},
        )

    active = suggestion(
        run_id=owned_run_id,
        user_id=test_user.id,
        suffix="active",
        status="new",
    )
    already_dismissed = suggestion(
        run_id=owned_run_id,
        user_id=test_user.id,
        suffix="dismissed",
        status="dismissed",
    )
    foreign = suggestion(
        run_id=other_run_id,
        user_id=other_user.id,
        suffix="foreign",
        status="new",
    )
    db_session.add_all([active, already_dismissed, foreign])
    db_session.commit()
    for row in (active, already_dismissed, foreign):
        db_session.refresh(row)

    response = dismiss_discovery_suggestions.clear_all(db_session, user_id=test_user.id)

    assert response.dismissed == [active.id]
    for row in (active, already_dismissed):
        db_session.refresh(row)
        assert row.status == "dismissed"
    db_session.refresh(foreign)
    assert foreign.status == "new"
