import pytest
from sqlalchemy import event

from app.models.db import UserScraperConfig
from app.models.internal.scraper_configs import normalize_aggregator_config
from app.services import scraper_configs
from app.services.scraper_configs import (
    CreateUserScraperConfig,
    ScraperConfigAlreadyExistsError,
    build_feed_payloads,
    create_user_scraper_config,
    create_user_scraper_config_in_session,
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


def test_canonical_existing_feed_uses_one_indexable_exact_query(db_session) -> None:
    existing = UserScraperConfig(
        user_id=1,
        scraper_type="substack",
        display_name="Canonical Feed",
        feed_url="https://example.com/feed",
        config={"feed_url": "https://example.com/feed"},
        is_active=True,
    )
    db_session.add(existing)
    db_session.commit()
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "user_scraper_configs" in statement and statement.lstrip().startswith("SELECT"):
            statements.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", capture_statement)
    try:
        matched = scraper_configs._find_existing_scraper_config(
            db_session,
            user_id=1,
            scraper_type="substack",
            feed_url="HTTPS://EXAMPLE.COM/feed/#fragment",
        )
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture_statement)

    assert matched is existing
    assert len(statements) == 1


@pytest.mark.parametrize(
    ("stored_feed_url", "stored_config"),
    [
        (
            "HTTPS://EXAMPLE.COM/feed/#historical-fragment",
            {"feed_url": "HTTPS://EXAMPLE.COM/feed/#historical-fragment", "limit": 10},
        ),
        (None, {"feed_url": "https://EXAMPLE.COM/feed/", "limit": 10}),
    ],
)
def test_create_reuses_historical_noncanonical_feed_identity(
    db_session,
    stored_feed_url,
    stored_config,
):
    historical = UserScraperConfig(
        user_id=1,
        scraper_type="substack",
        display_name="Historical Feed",
        feed_url=stored_feed_url,
        config=stored_config,
        is_active=True,
    )
    db_session.add(historical)
    db_session.commit()

    payload = CreateUserScraperConfig(
        scraper_type="substack",
        display_name="Duplicate Feed",
        config={"feed_url": "https://example.com/feed"},
        is_active=True,
    )
    with pytest.raises(ScraperConfigAlreadyExistsError) as exc_info:
        create_user_scraper_config_in_session(db_session, user_id=1, data=payload)

    assert exc_info.value.existing_config is historical
    assert db_session.query(UserScraperConfig).count() == 1


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


def test_aggregator_config_rejects_reddit_key():
    with pytest.raises(ValueError, match="unsupported aggregator key: reddit"):
        normalize_aggregator_config({"key": "reddit"})


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
