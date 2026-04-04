import pytest

from app.services.scraper_configs import (
    CreateUserScraperConfig,
    build_feed_payloads,
    create_user_scraper_config,
    list_active_configs_by_type,
    list_user_scraper_configs,
)

pytestmark = pytest.mark.usefixtures("stub_valid_feed_url")


def test_create_and_list_config(db_session):
    payload = CreateUserScraperConfig(
        scraper_type="substack",
        display_name="My Feed",
        config={"feed_url": "https://example.com/feed"},
        is_active=True,
    )
    created = create_user_scraper_config(db_session, user_id=1, data=payload)
    assert created.feed_url == "https://example.com/feed"

    configs = list_user_scraper_configs(db_session, user_id=1)
    assert len(configs) == 1
    assert configs[0].scraper_type == "substack"


def test_uniqueness_enforced(db_session):
    payload = CreateUserScraperConfig(
        scraper_type="substack",
        display_name="My Feed",
        config={"feed_url": "https://example.com/feed"},
        is_active=True,
    )
    create_user_scraper_config(db_session, user_id=1, data=payload)

    with pytest.raises(ValueError):
        create_user_scraper_config(db_session, user_id=1, data=payload)


def test_list_filtered_by_type(db_session):
    substack = CreateUserScraperConfig(
        scraper_type="substack",
        display_name="My Feed",
        config={"feed_url": "https://example.com/feed"},
        is_active=True,
    )
    podcast = CreateUserScraperConfig(
        scraper_type="podcast_rss",
        display_name="My Podcast",
        config={"feed_url": "https://pod.example.com/rss", "limit": 5},
        is_active=True,
    )
    create_user_scraper_config(db_session, user_id=1, data=substack)
    create_user_scraper_config(db_session, user_id=1, data=podcast)

    filtered = list_user_scraper_configs(db_session, user_id=1, allowed_types={"podcast_rss"})
    assert len(filtered) == 1
    assert filtered[0].scraper_type == "podcast_rss"

    active_podcast = list_active_configs_by_type(db_session, "podcast_rss")
    assert len(active_podcast) == 1
    assert active_podcast[0].config.get("limit") == 5

    payloads = build_feed_payloads(active_podcast, default_limit=10)
    assert payloads[0]["limit"] == 5


def test_create_youtube_config_accepts_channel_id(db_session):
    payload = CreateUserScraperConfig(
        scraper_type="youtube",
        display_name="YT Channel",
        config={"channel_id": "UC1234567890"},
        is_active=True,
    )
    created = create_user_scraper_config(db_session, user_id=1, data=payload)
    assert created.feed_url == "https://www.youtube.com/channel/UC1234567890"
    assert created.config.get("channel_id") == "UC1234567890"


def test_build_feed_payloads_apply_default_limit(db_session):
    payload = CreateUserScraperConfig(
        scraper_type="podcast_rss",
        display_name="No Limit",
        config={"feed_url": "https://pod.example.com/rss", "limit": None},
        is_active=True,
    )
    create_user_scraper_config(db_session, user_id=2, data=payload)

    active = list_active_configs_by_type(db_session, "podcast_rss")
    feed_payloads = build_feed_payloads(active, default_limit=12)
    assert feed_payloads[0]["limit"] == 12
    assert feed_payloads[0]["user_id"] == 2
