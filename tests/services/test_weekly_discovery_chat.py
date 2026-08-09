"""Tests for weekly discovery chat seeding."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.contracts import ContentStatus, ContentType
from app.models.db import (
    ChatMessage,
    ChatSession,
    Content,
    ContentReadStatus,
    FeedDiscoveryRun,
    FeedDiscoverySuggestion,
    OnboardingDiscoveryRun,
    UserScraperConfig,
)
from app.queries.chat_read_models import extract_messages_for_display
from app.services.chat_agent import create_processing_message
from app.services.weekly_discovery_chat import ensure_weekly_discovery_session


def test_ensure_weekly_discovery_session_creates_one_session_per_week(
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Test the weekly thread is deduplicated across multiple runs in the same week."""
    test_user.has_completed_onboarding = True
    db_session.commit()

    content = Content(
        url="https://example.com/robotics",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Robotics Weekly",
        source="Example",
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    db_session.add(
        ContentReadStatus(
            user_id=test_user.id,
            content_id=content.id,
            read_at=datetime(2026, 3, 8, 18, 0, tzinfo=UTC),
        )
    )
    db_session.add(
        OnboardingDiscoveryRun(
            user_id=test_user.id,
            status="completed",
            topic_summary="The user follows AI infrastructure and robotics.",
            inferred_topics=["AI infrastructure", "robotics"],
        )
    )
    db_session.add(FeedDiscoveryRun(user_id=test_user.id, status="completed"))
    db_session.commit()

    run = (
        db_session.query(FeedDiscoveryRun).filter(FeedDiscoveryRun.user_id == test_user.id).first()
    )
    assert run is not None

    db_session.add(
        FeedDiscoverySuggestion(
            run_id=run.id,
            user_id=test_user.id,
            suggestion_type="rss",
            feed_url="https://example.com/feed.xml",
            title="AI Robotics Feed",
            rationale="It overlaps with recent AI robotics reading.",
            status="new",
            score=cast(Any, 0.91),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.weekly_discovery_chat._user_local_date",
        lambda user, reference_time=None: date(2026, 3, 8),
    )

    first_session = ensure_weekly_discovery_session(db_session, user_id=test_user.id)
    second_session = ensure_weekly_discovery_session(db_session, user_id=test_user.id)

    assert first_session is not None
    assert second_session is not None
    assert second_session.id == first_session.id
    assert first_session.session_type == "weekly_discovery"
    assert first_session.topic == "weekly:2026-03-08"
    assert first_session.title == "Weekly Discovery • 2026-03-08"
    assert "Fresh discovery suggestions in canonical numbered order" in (
        first_session.context_snapshot or ""
    )

    sessions = (
        db_session.query(ChatSession)
        .filter(
            ChatSession.user_id == test_user.id,
            ChatSession.session_type == "weekly_discovery",
        )
        .all()
    )
    assert len(sessions) == 1


def test_ensure_weekly_discovery_session_uses_onboarding_fallback_without_suggestions(
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Test weekly discovery still creates a thread for cold-start users."""
    test_user.has_completed_onboarding = True
    db_session.commit()

    db_session.add(
        OnboardingDiscoveryRun(
            user_id=test_user.id,
            status="completed",
            topic_summary="The user likes developer tools and economics.",
            inferred_topics=["developer tools", "economics"],
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.weekly_discovery_chat._user_local_date",
        lambda user, reference_time=None: date(2026, 3, 12),
    )

    session = ensure_weekly_discovery_session(db_session, user_id=test_user.id)

    assert session is not None
    assert session.topic == "weekly:2026-03-08"

    refreshed = db_session.query(ChatSession).filter(ChatSession.id == session.id).first()
    assert refreshed is not None
    assert "Onboarding summary:" in (refreshed.context_snapshot or "")


def test_ensure_weekly_discovery_session_uses_summary_display_titles_for_recent_reads(
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Recent read seed context should prefer summary titles over stored content titles."""
    test_user.has_completed_onboarding = True
    db_session.commit()

    content = Content(
        url="https://example.com/robotics",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Stored page title",
        source="Example",
        content_metadata={
            "summary": {
                "title": "Canonical summary title",
                "overview": "Robotics summary",
            }
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    db_session.add(
        ContentReadStatus(
            user_id=test_user.id,
            content_id=content.id,
            read_at=datetime(2026, 3, 8, 18, 0, tzinfo=UTC),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.weekly_discovery_chat._user_local_date",
        lambda user, reference_time=None: date(2026, 3, 8),
    )

    session = ensure_weekly_discovery_session(db_session, user_id=test_user.id)

    assert session is not None
    assert session.context_snapshot is not None
    assert "Canonical summary title" in session.context_snapshot
    assert "Stored page title" not in session.context_snapshot


def test_weekly_discovery_options_are_rendered_and_frozen_for_ordinal_follow_up(
    client: TestClient,
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Seed cards and queued follow-ups must share one canonical numbered identity."""
    test_user.has_completed_onboarding = True
    db_session.commit()

    run = FeedDiscoveryRun(user_id=test_user.id, status="completed")
    db_session.add(run)
    db_session.flush()
    db_session.add_all(
        [
            FeedDiscoverySuggestion(
                run_id=run.id,
                user_id=test_user.id,
                suggestion_type="youtube",
                site_url="https://WWW.YouTube.com/channel/UC1234567890/",
                feed_url="https://WWW.YouTube.com/channel/UC1234567890/",
                title="Robotics Channel",
                description="Weekly robotics field notes.",
                rationale="Strong match for robotics.",
                status="new",
                score=cast(Any, 0.95),
            ),
            FeedDiscoverySuggestion(
                run_id=run.id,
                user_id=test_user.id,
                suggestion_type="rss",
                site_url="HTTPS://Example.COM/",
                feed_url="HTTPS://Example.COM/feed.xml/",
                title="AI Systems Blog",
                description="Detailed systems writing.",
                rationale="Strong match for AI infrastructure.",
                status="new",
                score=cast(Any, 0.95),
            ),
            FeedDiscoverySuggestion(
                run_id=run.id,
                user_id=test_user.id,
                suggestion_type="podcast_rss",
                site_url="https://podcasts.example.com/show/",
                feed_url="https://podcasts.example.com/show/feed.xml",
                title="Systems Podcast",
                rationale="A related audio option.",
                status="new",
                score=cast(Any, 0.7),
            ),
        ]
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.weekly_discovery_chat._user_local_date",
        lambda user, reference_time=None: date(2026, 3, 8),
    )

    session = ensure_weekly_discovery_session(db_session, user_id=test_user.id)

    assert session is not None
    assert session.id is not None
    context_snapshot = session.context_snapshot or ""
    expected_first = """1. Robotics Channel
   suggestion_type=youtube
   feed_url=https://www.youtube.com/channel/UC1234567890
   site_url=https://www.youtube.com/channel/UC1234567890"""
    expected_second = """2. AI Systems Blog
   suggestion_type=atom
   feed_url=https://example.com/feed.xml
   site_url=https://example.com"""
    assert expected_first in context_snapshot
    assert expected_second in context_snapshot
    assert context_snapshot.index(expected_first) < context_snapshot.index(expected_second)

    display_messages = extract_messages_for_display(
        db_session,
        session.id,
        user_id=test_user.id,
    )
    assert len(display_messages) == 1
    assert display_messages[0].role.value == "assistant"
    assert [option.feed_type.value for option in display_messages[0].feed_options] == [
        "youtube",
        "atom",
        "podcast_rss",
    ]
    assert display_messages[0].feed_options[1].feed_format.value == "rss"

    detail_response = client.get(f"/api/content/chat/sessions/{session.id}")
    assert detail_response.status_code == 200
    detail_options = detail_response.json()["messages"][0]["feed_options"]
    assert [option["feed_type"] for option in detail_options] == [
        "youtube",
        "atom",
        "podcast_rss",
    ]
    assert detail_options[0]["feed_url"] == ("https://www.youtube.com/channel/UC1234567890")
    assert detail_options[1]["feed_format"] == "rss"
    assert all(option["is_subscribed"] is False for option in detail_options)

    follow_up_response = client.post(
        f"/api/content/chat/sessions/{session.id}/messages",
        json={"message": "add first two"},
    )
    assert follow_up_response.status_code == 200
    queued_message = db_session.get(ChatMessage, follow_up_response.json()["message_id"])
    assert queued_message is not None
    assert isinstance(queued_message.processing_context, dict)
    assert queued_message.processing_context["user_prompt"] == "add first two"
    assert queued_message.processing_context["session"]["context_snapshot"] == context_snapshot


def test_weekly_discovery_options_filter_malformed_duplicates_and_unsupported_types(
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Only unique actionable canonical identities should reach cards or context."""
    test_user.has_completed_onboarding = True
    run = FeedDiscoveryRun(user_id=test_user.id, status="completed")
    db_session.add(run)
    db_session.flush()
    db_session.add_all(
        [
            FeedDiscoverySuggestion(
                run_id=run.id,
                user_id=test_user.id,
                suggestion_type="rss",
                site_url="not a site URL",
                feed_url="HTTPS://Example.COM/feed.xml/",
                title="Canonical legacy RSS",
                status="new",
                score=cast(Any, 1.0),
            ),
            FeedDiscoverySuggestion(
                run_id=run.id,
                user_id=test_user.id,
                suggestion_type="atom",
                site_url="https://duplicate.example.com",
                feed_url="https://example.com/feed.xml",
                title="Duplicate canonical URL",
                status="new",
                score=cast(Any, 0.9),
            ),
            FeedDiscoverySuggestion(
                run_id=run.id,
                user_id=test_user.id,
                suggestion_type="atom",
                feed_url="not a URL",
                title="Malformed URL",
                status="new",
                score=cast(Any, 0.8),
            ),
            FeedDiscoverySuggestion(
                run_id=run.id,
                user_id=test_user.id,
                suggestion_type="future_feed_type",
                feed_url="https://unsupported.example.com/feed",
                title="Unsupported type",
                status="new",
                score=cast(Any, 0.7),
            ),
            FeedDiscoverySuggestion(
                run_id=run.id,
                user_id=test_user.id,
                suggestion_type="substack",
                site_url="https://writer.example.com/",
                feed_url="https://writer.example.com/feed/",
                title="Valid newsletter",
                status="new",
                score=cast(Any, 0.6),
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.weekly_discovery_chat._user_local_date",
        lambda user, reference_time=None: date(2026, 3, 8),
    )

    session = ensure_weekly_discovery_session(db_session, user_id=test_user.id)

    assert session is not None
    assert session.id is not None
    display_messages = extract_messages_for_display(
        db_session,
        session.id,
        user_id=test_user.id,
    )
    options = display_messages[0].feed_options
    assert [(option.title, option.feed_type.value) for option in options] == [
        ("Canonical legacy RSS", "atom"),
        ("Valid newsletter", "substack"),
    ]
    assert options[0].feed_url == "https://example.com/feed.xml"
    assert options[0].site_url == options[0].feed_url
    assert len({option.id for option in options}) == 2

    context_snapshot = session.context_snapshot or ""
    assert "1. Canonical legacy RSS" in context_snapshot
    assert "2. Valid newsletter" in context_snapshot
    assert "3." not in context_snapshot
    assert "Duplicate canonical URL" not in context_snapshot
    assert "Malformed URL" not in context_snapshot
    assert "Unsupported type" not in context_snapshot


def test_weekly_discovery_seed_failure_rolls_back_session_and_message(
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """A failed seed write must not leave an empty weekly chat dead end."""
    test_user.has_completed_onboarding = True
    db_session.commit()
    monkeypatch.setattr(
        "app.services.weekly_discovery_chat._user_local_date",
        lambda user, reference_time=None: date(2026, 3, 8),
    )

    def _fail_seed(*_args, **_kwargs) -> None:
        raise RuntimeError("seed write failed")

    monkeypatch.setattr(
        "app.services.weekly_discovery_chat.seed_assistant_message",
        _fail_seed,
    )

    with pytest.raises(RuntimeError, match="seed write failed"):
        ensure_weekly_discovery_session(db_session, user_id=test_user.id)

    assert (
        db_session.query(ChatSession).filter(ChatSession.session_type == "weekly_discovery").count()
        == 0
    )
    assert db_session.query(ChatMessage).count() == 0


def test_unengaged_weekly_session_reprojects_late_discovery_suggestions(
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """A later discovery run should refresh the untouched seed without a new session."""
    test_user.has_completed_onboarding = True
    db_session.commit()
    monkeypatch.setattr(
        "app.services.weekly_discovery_chat._user_local_date",
        lambda user, reference_time=None: date(2026, 3, 8),
    )

    initial_session = ensure_weekly_discovery_session(db_session, user_id=test_user.id)

    assert initial_session is not None
    assert initial_session.id is not None
    session_id = int(initial_session.id)
    assert "Fresh discovery suggestions" not in (initial_session.context_snapshot or "")

    run = FeedDiscoveryRun(user_id=test_user.id, status="completed")
    db_session.add(run)
    db_session.flush()
    db_session.add(
        FeedDiscoverySuggestion(
            run_id=run.id,
            user_id=test_user.id,
            suggestion_type="rss",
            site_url="https://example.com",
            feed_url="https://example.com/feed.xml",
            title="Late Discovery Feed",
            rationale="The enrichment worker finished second.",
            status="new",
            score=cast(Any, 0.98),
        )
    )
    db_session.commit()

    reprojected = ensure_weekly_discovery_session(db_session, user_id=test_user.id)

    assert reprojected is not None
    assert reprojected.id == session_id
    assert "1. Late Discovery Feed" in (reprojected.context_snapshot or "")
    message_rows = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.asc())
        .all()
    )
    assert len(message_rows) == 1
    display_messages = extract_messages_for_display(
        db_session,
        session_id,
        user_id=test_user.id,
    )
    assert len(display_messages) == 1
    assert [option.title for option in display_messages[0].feed_options] == ["Late Discovery Feed"]

    reprojected_message_id = message_rows[0].id
    reprojected_at = reprojected.last_message_at
    repeated = ensure_weekly_discovery_session(db_session, user_id=test_user.id)
    repeated_message = (
        db_session.query(ChatMessage).filter(ChatMessage.session_id == session_id).one()
    )

    assert repeated is not None
    assert repeated.id == session_id
    assert repeated_message.id == reprojected_message_id
    assert repeated.last_message_at == reprojected_at


def test_engaged_weekly_session_is_never_reprojected(
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Late discovery results must not rewrite a conversation after a user turn."""
    test_user.has_completed_onboarding = True
    db_session.commit()
    monkeypatch.setattr(
        "app.services.weekly_discovery_chat._user_local_date",
        lambda user, reference_time=None: date(2026, 3, 8),
    )

    initial_session = ensure_weekly_discovery_session(db_session, user_id=test_user.id)

    assert initial_session is not None
    assert initial_session.id is not None
    session_id = int(initial_session.id)
    original_context = initial_session.context_snapshot
    original_seed = db_session.query(ChatMessage).filter(ChatMessage.session_id == session_id).one()
    original_seed_id = original_seed.id
    original_seed_payload = original_seed.message_list
    create_processing_message(db_session, session_id, "Find me more robotics feeds")

    run = FeedDiscoveryRun(user_id=test_user.id, status="completed")
    db_session.add(run)
    db_session.flush()
    db_session.add(
        FeedDiscoverySuggestion(
            run_id=run.id,
            user_id=test_user.id,
            suggestion_type="rss",
            site_url="https://robotics.example.com",
            feed_url="https://robotics.example.com/feed.xml",
            title="Robotics After Engagement",
            status="new",
            score=cast(Any, 0.99),
        )
    )
    db_session.commit()

    preserved = ensure_weekly_discovery_session(db_session, user_id=test_user.id)

    assert preserved is not None
    assert preserved.id == session_id
    assert preserved.context_snapshot == original_context
    message_rows = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.asc())
        .all()
    )
    assert len(message_rows) == 2
    assert message_rows[0].id == original_seed_id
    assert message_rows[0].message_list == original_seed_payload
    assert message_rows[1].status == "processing"
    assert (
        extract_messages_for_display(
            db_session,
            session_id,
            user_id=test_user.id,
        )[0].feed_options
        == []
    )


def test_weekly_discovery_seed_excludes_active_subscriptions(
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Persisted subscriptions must not return as actionable discovery cards."""
    test_user.has_completed_onboarding = True
    run = FeedDiscoveryRun(user_id=test_user.id, status="completed")
    db_session.add(run)
    db_session.flush()
    db_session.add_all(
        [
            FeedDiscoverySuggestion(
                run_id=run.id,
                user_id=test_user.id,
                suggestion_type="rss",
                feed_url="HTTPS://Example.COM/feed.xml/",
                title="Already Added",
                status="new",
                score=cast(Any, 1.0),
            ),
            FeedDiscoverySuggestion(
                run_id=run.id,
                user_id=test_user.id,
                suggestion_type="rss",
                feed_url="https://fresh.example.com/feed.xml",
                title="Fresh Option",
                status="new",
                score=cast(Any, 0.9),
            ),
            UserScraperConfig(
                user_id=test_user.id,
                scraper_type="atom",
                display_name="Already Added",
                feed_url="https://example.com/feed.xml",
                config={"feed_url": "https://example.com/feed.xml"},
                is_active=True,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.weekly_discovery_chat._user_local_date",
        lambda user, reference_time=None: date(2026, 3, 8),
    )

    session = ensure_weekly_discovery_session(db_session, user_id=test_user.id)

    assert session is not None
    assert session.id is not None
    messages = extract_messages_for_display(
        db_session,
        int(session.id),
        user_id=test_user.id,
    )
    assert [option.title for option in messages[0].feed_options] == ["Fresh Option"]
    assert "Already Added" not in (session.context_snapshot or "")


def test_engaged_weekly_card_projects_later_subscription_without_rewriting_seed(
    client: TestClient,
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Existing cards should reflect a later subscription without changing chat history."""
    test_user.has_completed_onboarding = True
    run = FeedDiscoveryRun(user_id=test_user.id, status="completed")
    db_session.add(run)
    db_session.flush()
    db_session.add(
        FeedDiscoverySuggestion(
            run_id=run.id,
            user_id=test_user.id,
            suggestion_type="rss",
            site_url="https://signals.example.com",
            feed_url="https://signals.example.com/feed.xml",
            title="Signals Weekly",
            status="new",
            score=cast(Any, 0.99),
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.weekly_discovery_chat._user_local_date",
        lambda user, reference_time=None: date(2026, 3, 8),
    )

    session = ensure_weekly_discovery_session(db_session, user_id=test_user.id)

    assert session is not None
    assert session.id is not None
    session_id = int(session.id)
    seed_message = db_session.query(ChatMessage).filter(ChatMessage.session_id == session_id).one()
    assert seed_message.id is not None
    seed_message_id = int(seed_message.id)
    original_context = session.context_snapshot
    original_message_list = seed_message.message_list
    original_render_metadata = deepcopy(seed_message.render_metadata)

    create_processing_message(db_session, session_id, "Tell me about the first option")
    db_session.add(
        UserScraperConfig(
            user_id=test_user.id,
            scraper_type="atom",
            display_name="Signals Weekly",
            feed_url="HTTPS://SIGNALS.EXAMPLE.COM/feed.xml/",
            config={"feed_url": "HTTPS://SIGNALS.EXAMPLE.COM/feed.xml/"},
            is_active=True,
        )
    )
    db_session.commit()

    preserved = ensure_weekly_discovery_session(db_session, user_id=test_user.id)

    assert preserved is not None
    assert preserved.context_snapshot == original_context
    persisted_seed = db_session.get(ChatMessage, seed_message_id)
    assert persisted_seed is not None
    assert persisted_seed.message_list == original_message_list
    assert persisted_seed.render_metadata == original_render_metadata

    detail_response = client.get(f"/api/content/chat/sessions/{session_id}")
    assert detail_response.status_code == 200
    detail_option = detail_response.json()["messages"][0]["feed_options"][0]
    assert detail_option["title"] == "Signals Weekly"
    assert detail_option["is_subscribed"] is True

    status_response = client.get(f"/api/content/chat/messages/{seed_message_id}/status")
    assert status_response.status_code == 200
    status_option = status_response.json()["assistant_message"]["feed_options"][0]
    assert status_option["is_subscribed"] is True

    db_session.refresh(persisted_seed)
    assert persisted_seed.render_metadata == original_render_metadata
