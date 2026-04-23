"""Tests for fast-news aggregator persistence during onboarding completion."""

from __future__ import annotations

from app.constants import AGGREGATOR_FEED_URL_PREFIX, AGGREGATOR_SCRAPER_TYPE
from app.models.api.common import OnboardingSelectedAggregator
from app.models.schema import UserScraperConfig
from app.services.onboarding import _create_aggregator_configs


def test_create_aggregator_configs_persists_per_user_subscription(db_session, test_user) -> None:
    user_id = test_user.id
    assert user_id is not None

    aggregators = [
        OnboardingSelectedAggregator(key="hackernews", title="Hacker News"),
        OnboardingSelectedAggregator(
            key="brutalist",
            title="Brutalist Report",
            topics=["Science", "Sports"],
        ),
    ]

    count = _create_aggregator_configs(db_session, user_id, aggregators)
    db_session.commit()

    rows = (
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == user_id)
        .filter(UserScraperConfig.scraper_type == AGGREGATOR_SCRAPER_TYPE)
        .order_by(UserScraperConfig.id.asc())
        .all()
    )

    assert count == 2
    assert {row.feed_url for row in rows} == {
        f"{AGGREGATOR_FEED_URL_PREFIX}hackernews",
        f"{AGGREGATOR_FEED_URL_PREFIX}brutalist",
    }
    by_key = {row.config["key"]: row for row in rows}
    assert by_key["hackernews"].config.get("topics") in (None, [])
    # Topics are normalized to lowercase + sorted by the validator.
    assert by_key["brutalist"].config["topics"] == ["science", "sports"]


def test_create_aggregator_configs_is_idempotent(db_session, test_user) -> None:
    user_id = test_user.id
    assert user_id is not None

    aggregator = [OnboardingSelectedAggregator(key="techmeme", title="Techmeme")]
    _create_aggregator_configs(db_session, user_id, aggregator)
    db_session.commit()

    count_again = _create_aggregator_configs(db_session, user_id, aggregator)
    db_session.commit()

    rows = (
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == user_id)
        .filter(UserScraperConfig.scraper_type == AGGREGATOR_SCRAPER_TYPE)
        .all()
    )
    assert count_again == 1
    assert len(rows) == 1
