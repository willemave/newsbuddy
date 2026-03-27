"""Tests for detected feed subscription helper."""

from app.constants import DEFAULT_NEW_FEED_LIMIT
from app.models.schema import UserScraperConfig
from app.services.feed_subscription import (
    subscribe_to_detected_feed,
    subscribe_to_detected_feed_result,
)


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
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.id == result.config_id)
        .first()
    )
    assert record is not None
    assert record.feed_url == "https://example.com/second-feed.xml"
