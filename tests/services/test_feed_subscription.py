"""Tests for detected feed subscription helper."""

import pytest

from app.constants import DEFAULT_NEW_FEED_LIMIT
from app.models.db import ProcessingTask, UserScraperConfig
from app.services.feed_subscription import (
    load_active_feed_urls,
    subscribe_to_detected_feed,
    subscribe_to_detected_feed_result,
)

pytestmark = pytest.mark.usefixtures("stub_valid_feed_url")


def test_load_active_feed_urls_scopes_filters_and_canonicalizes(
    db_session,
    test_user,
    user_factory,
) -> None:
    other_user = user_factory()
    db_session.add_all(
        [
            UserScraperConfig(
                user_id=test_user.id,
                scraper_type="atom",
                display_name="Active Atom",
                config={},
                feed_url="https://EXAMPLE.com/feed.xml/",
                is_active=True,
            ),
            UserScraperConfig(
                user_id=test_user.id,
                scraper_type="podcast_rss",
                display_name="Active Podcast",
                config={},
                feed_url="https://pod.example.com/show.rss",
                is_active=True,
            ),
            UserScraperConfig(
                user_id=test_user.id,
                scraper_type="atom",
                display_name="Inactive",
                config={},
                feed_url="https://example.com/inactive.xml",
                is_active=False,
            ),
            UserScraperConfig(
                user_id=test_user.id,
                scraper_type="atom",
                display_name="Blank",
                config={},
                feed_url="   ",
                is_active=True,
            ),
            UserScraperConfig(
                user_id=test_user.id,
                scraper_type="atom",
                display_name="Null",
                config={},
                feed_url=None,
                is_active=True,
            ),
            UserScraperConfig(
                user_id=other_user.id,
                scraper_type="atom",
                display_name="Other User",
                config={},
                feed_url="https://other.example/feed.xml",
                is_active=True,
            ),
        ]
    )
    db_session.commit()

    assert load_active_feed_urls(db_session, user_id=test_user.id) == {
        "https://example.com/feed.xml",
        "https://pod.example.com/show.rss",
    }
    assert load_active_feed_urls(
        db_session,
        user_id=test_user.id,
        feed_type="atom",
    ) == {"https://example.com/feed.xml"}


def test_subscribe_to_detected_feed_creates_config(db_session, test_user):
    feed = {"url": "https://example.com/feed.xml", "type": "atom", "title": "Example Feed"}

    created, status = subscribe_to_detected_feed(
        db_session,
        test_user.id,
        feed,
        display_name="Example Feed",
    )

    assert created is True
    assert status == "created"

    record = (
        db_session.query(UserScraperConfig)
        .filter(
            UserScraperConfig.user_id == test_user.id,
            UserScraperConfig.feed_url == "https://example.com/feed.xml",
        )
        .first()
    )
    assert record is not None
    assert record.scraper_type == "atom"
    assert record.display_name == "Example Feed"
    assert record.config.get("limit") == DEFAULT_NEW_FEED_LIMIT


def test_subscribe_to_detected_feed_falls_back_to_feed_domain(db_session, test_user):
    feed = {"url": "https://registerspill.thorstenball.com/feed", "type": "substack", "title": None}

    created, status = subscribe_to_detected_feed(
        db_session,
        test_user.id,
        feed,
        display_name=None,
    )

    assert created is True
    assert status == "created"

    record = (
        db_session.query(UserScraperConfig)
        .filter(
            UserScraperConfig.user_id == test_user.id,
            UserScraperConfig.feed_url == "https://registerspill.thorstenball.com/feed",
        )
        .first()
    )
    assert record is not None
    assert record.display_name == "registerspill.thorstenball.com"


def test_subscribe_to_detected_feed_result_includes_created_config_id(
    db_session,
    test_user,
):
    feed = {"url": "https://example.com/second-feed.xml", "type": "atom", "title": "Second Feed"}

    result = subscribe_to_detected_feed_result(
        db_session,
        test_user.id,
        feed,
        display_name="Second Feed",
    )

    assert result.created is True
    assert result.status == "created"
    assert isinstance(result.config_id, int)

    record = (
        db_session.query(UserScraperConfig).filter(UserScraperConfig.id == result.config_id).first()
    )
    assert record is not None
    assert record.feed_url == "https://example.com/second-feed.xml"


def test_subscribe_to_detected_feed_skips_inactive_user(db_session, user_factory):
    user = user_factory(is_active=False)
    feed = {"url": "https://example.com/inactive.xml", "type": "atom", "title": "Old Feed"}

    result = subscribe_to_detected_feed_result(
        db_session,
        user.id,
        feed,
        display_name="Old Feed",
    )

    assert result.created is False
    assert result.status == "inactive_user"
    assert db_session.query(UserScraperConfig).count() == 0
    assert db_session.query(ProcessingTask).count() == 0


def test_subscribe_to_detected_feed_reactivates_inactive_config(db_session, test_user):
    config = UserScraperConfig(
        user_id=test_user.id,
        scraper_type="atom",
        display_name="Paused Feed",
        config={"feed_url": "https://example.com/paused.xml", "limit": 10},
        feed_url="https://example.com/paused.xml",
        is_active=False,
    )
    db_session.add(config)
    db_session.commit()

    result = subscribe_to_detected_feed_result(
        db_session,
        test_user.id,
        {
            "url": "https://EXAMPLE.COM/paused.xml/",
            "type": "atom",
            "title": "Paused Feed",
        },
    )

    db_session.refresh(config)
    assert result.created is False
    assert result.status == "reactivated"
    assert result.config_id == config.id
    assert isinstance(result.backfill_task_id, int)
    assert config.is_active is True
