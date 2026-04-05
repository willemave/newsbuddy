from __future__ import annotations

from app.models.api.common import OnboardingFastDiscoverResponse, OnboardingSuggestion
from app.models.schema import FeedDiscoveryRun, FeedDiscoverySuggestion
from app.services.onboarding import _persist_discovery_run


def test_persist_discovery_run_deduplicates_within_response(db_session, test_user) -> None:
    suggestions = OnboardingFastDiscoverResponse(
        recommended_substacks=[
            OnboardingSuggestion(
                suggestion_type="substack",
                title="Conservation Realist",
                feed_url="https://conservationrealist.substack.com/feed",
            )
        ],
        recommended_pods=[
            OnboardingSuggestion(
                suggestion_type="podcast_rss",
                title="Conservation Realist Podcast",
                feed_url="https://conservationrealist.substack.com/feed",
            )
        ],
    )

    run_id = _persist_discovery_run(db_session, test_user.id, suggestions)

    assert run_id is not None
    persisted = (
        db_session.query(FeedDiscoverySuggestion)
        .filter(FeedDiscoverySuggestion.user_id == test_user.id)
        .all()
    )
    assert len(persisted) == 1
    assert persisted[0].feed_url == "https://conservationrealist.substack.com/feed"


def test_persist_discovery_run_returns_none_when_all_feeds_already_exist(
    db_session, test_user
) -> None:
    existing_run = FeedDiscoveryRun(
        user_id=test_user.id,
        status="completed",
        direction_summary="existing",
        seed_content_ids=[],
    )
    db_session.add(existing_run)
    db_session.flush()
    db_session.add(
        FeedDiscoverySuggestion(
            run_id=existing_run.id,
            user_id=test_user.id,
            suggestion_type="substack",
            site_url="https://conservationrealist.substack.com",
            feed_url="https://conservationrealist.substack.com/feed",
            title="Conservation Realist",
            status="new",
            config={"feed_url": "https://conservationrealist.substack.com/feed"},
        )
    )
    db_session.commit()

    suggestions = OnboardingFastDiscoverResponse(
        recommended_substacks=[
            OnboardingSuggestion(
                suggestion_type="substack",
                title="Conservation Realist",
                feed_url="https://conservationrealist.substack.com/feed",
            )
        ]
    )

    run_id = _persist_discovery_run(db_session, test_user.id, suggestions)

    assert run_id is None
    runs = db_session.query(FeedDiscoveryRun).filter(FeedDiscoveryRun.user_id == test_user.id).all()
    persisted = (
        db_session.query(FeedDiscoverySuggestion)
        .filter(FeedDiscoverySuggestion.user_id == test_user.id)
        .all()
    )
    assert len(runs) == 1
    assert len(persisted) == 1
