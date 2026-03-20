"""Tests for weekly discovery chat seeding."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import (
    ChatSession,
    Content,
    ContentReadStatus,
    FeedDiscoveryRun,
    FeedDiscoverySuggestion,
    OnboardingDiscoveryRun,
)
from app.services.weekly_discovery_chat import ensure_weekly_discovery_session


def test_ensure_weekly_discovery_session_creates_one_session_per_week(
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Test the weekly thread is deduplicated across multiple runs in the same week."""
    test_user.has_completed_onboarding = True
    test_user.news_digest_timezone = "America/Los_Angeles"
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
        db_session.query(FeedDiscoveryRun)
        .filter(FeedDiscoveryRun.user_id == test_user.id)
        .first()
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
            score=0.91,
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
    assert "Fresh discovery suggestions:" in (first_session.context_snapshot or "")

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
