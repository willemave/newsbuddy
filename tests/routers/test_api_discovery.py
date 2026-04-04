from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.constants import DEFAULT_NEW_FEED_LIMIT
from app.models.schema import Content, FeedDiscoveryRun, FeedDiscoverySuggestion, UserScraperConfig
from app.models.user import User
from app.services.podcast_search import PodcastEpisodeSearchHit

pytestmark = pytest.mark.usefixtures("stub_valid_feed_url")


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


def _build_podcast_hit(
    *,
    title: str,
    episode_url: str,
    feed_url: str | None,
    provider: str = "listen_notes",
) -> PodcastEpisodeSearchHit:
    return PodcastEpisodeSearchHit(
        title=title,
        episode_url=episode_url,
        podcast_title="AI Weekly",
        source="example.fm",
        snippet="Test snippet",
        feed_url=feed_url,
        published_at="2026-02-19T00:00:00Z",
        provider=provider,
        score=1.12,
    )


def test_get_discovery_suggestions_grouped(client, db_session, test_user):
    run = _create_run(db_session, test_user.id)
    suggestions = [
        FeedDiscoverySuggestion(
            run_id=run.id,
            user_id=test_user.id,
            suggestion_type="atom",
            site_url="https://example.com",
            feed_url="https://example.com/feed.xml",
            title="Example Feed",
            status="new",
            config={"feed_url": "https://example.com/feed.xml"},
        ),
        FeedDiscoverySuggestion(
            run_id=run.id,
            user_id=test_user.id,
            suggestion_type="podcast_rss",
            site_url="https://pod.example.com",
            feed_url="https://pod.example.com/rss.xml",
            title="Example Podcast",
            status="new",
            config={"feed_url": "https://pod.example.com/rss.xml"},
        ),
        FeedDiscoverySuggestion(
            run_id=run.id,
            user_id=test_user.id,
            suggestion_type="youtube",
            site_url="https://www.youtube.com/channel/UC123",
            feed_url="https://www.youtube.com/channel/UC123",
            title="Example YouTube",
            status="new",
            config={"feed_url": "https://www.youtube.com/channel/UC123", "channel_id": "UC123"},
        ),
    ]
    db_session.add_all(suggestions)
    db_session.commit()

    response = client.get("/api/discovery/suggestions")
    assert response.status_code == 200
    data = response.json()
    assert len(data["feeds"]) == 1
    assert len(data["podcasts"]) == 1
    assert len(data["youtube"]) == 1


def test_discovery_subscribe_creates_config(client, db_session, test_user):
    run = _create_run(db_session, test_user.id)
    suggestion = FeedDiscoverySuggestion(
        run_id=run.id,
        user_id=test_user.id,
        suggestion_type="substack",
        site_url="https://example.substack.com",
        feed_url="https://example.substack.com/feed",
        title="Example Substack",
        status="new",
        config={"feed_url": "https://example.substack.com/feed"},
    )
    db_session.add(suggestion)
    db_session.commit()
    db_session.refresh(suggestion)

    response = client.post(
        "/api/discovery/subscribe",
        json={"suggestion_ids": [suggestion.id]},
    )
    assert response.status_code == 200
    data = response.json()
    assert suggestion.id in data["subscribed"]

    config = (
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == test_user.id)
        .first()
    )
    assert config is not None
    assert config.feed_url == "https://example.substack.com/feed"
    assert config.config.get("limit") == DEFAULT_NEW_FEED_LIMIT


def test_discovery_subscribe_uses_feed_url_when_missing_in_config(
    client,
    db_session,
    test_user,
):
    run = _create_run(db_session, test_user.id)
    suggestion = FeedDiscoverySuggestion(
        run_id=run.id,
        user_id=test_user.id,
        suggestion_type="podcast_rss",
        site_url="https://podcasts.apple.com/us/podcast/example/id123",
        feed_url="https://example.com/podcast/rss.xml",
        title="Example Podcast",
        status="new",
        config={"source": "apple_podcasts", "podcast_id": "123"},
    )
    db_session.add(suggestion)
    db_session.commit()
    db_session.refresh(suggestion)

    response = client.post(
        "/api/discovery/subscribe",
        json={"suggestion_ids": [suggestion.id]},
    )
    assert response.status_code == 200
    data = response.json()
    assert suggestion.id in data["subscribed"]

    config = (
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == test_user.id)
        .first()
    )
    assert config is not None
    assert config.feed_url == "https://example.com/podcast/rss.xml"
    assert config.config.get("limit") == DEFAULT_NEW_FEED_LIMIT


def test_discovery_dismiss_marks_suggestion(client, db_session, test_user):
    run = _create_run(db_session, test_user.id)
    suggestion = FeedDiscoverySuggestion(
        run_id=run.id,
        user_id=test_user.id,
        suggestion_type="atom",
        site_url="https://example.com",
        feed_url="https://example.com/feed.xml",
        title="Example Feed",
        status="new",
        config={"feed_url": "https://example.com/feed.xml"},
    )
    db_session.add(suggestion)
    db_session.commit()
    db_session.refresh(suggestion)

    response = client.post(
        "/api/discovery/dismiss",
        json={"suggestion_ids": [suggestion.id]},
    )
    assert response.status_code == 200

    db_session.refresh(suggestion)
    assert suggestion.status == "dismissed"


def test_discovery_add_item_creates_content(client, db_session, test_user):
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

    response = client.post(
        "/api/discovery/add-item",
        json={"suggestion_ids": [suggestion.id]},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["created"]) == 1

    content = db_session.query(Content).filter(Content.url == suggestion.item_url).first()
    assert content is not None


def test_discovery_history_groups_runs(client, db_session, test_user):
    older_run = FeedDiscoveryRun(
        user_id=test_user.id,
        status="completed",
        direction_summary="Older summary",
        seed_content_ids=[],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    newer_run = FeedDiscoveryRun(
        user_id=test_user.id,
        status="completed",
        direction_summary="Newer summary",
        seed_content_ids=[],
        created_at=datetime(2026, 1, 8, tzinfo=UTC),
    )
    db_session.add_all([older_run, newer_run])
    db_session.commit()
    db_session.refresh(older_run)
    db_session.refresh(newer_run)

    older_suggestion = FeedDiscoverySuggestion(
        run_id=older_run.id,
        user_id=test_user.id,
        suggestion_type="atom",
        site_url="https://older.example.com",
        feed_url="https://older.example.com/feed.xml",
        title="Older Feed",
        status="new",
        config={"feed_url": "https://older.example.com/feed.xml"},
    )
    newer_suggestion = FeedDiscoverySuggestion(
        run_id=newer_run.id,
        user_id=test_user.id,
        suggestion_type="podcast_rss",
        site_url="https://newer.example.com",
        feed_url="https://newer.example.com/rss.xml",
        title="Newer Podcast",
        status="new",
        config={"feed_url": "https://newer.example.com/rss.xml"},
    )
    db_session.add_all([older_suggestion, newer_suggestion])
    db_session.commit()

    response = client.get("/api/discovery/history?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["runs"]) == 2
    assert data["runs"][0]["run_id"] == newer_run.id
    assert len(data["runs"][0]["podcasts"]) == 1
    assert data["runs"][1]["run_id"] == older_run.id
    assert len(data["runs"][1]["feeds"]) == 1


def test_discovery_podcast_search_returns_results(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.api.discovery.search_podcast_episodes",
        lambda query, limit: [
            PodcastEpisodeSearchHit(
                title="AI Founder Interview",
                episode_url="https://example.fm/episodes/founder-interview",
                podcast_title="AI Weekly",
                source="example.fm",
                snippet="An interview about AI startups.",
                feed_url="https://example.fm/rss",
                published_at="2026-02-19T00:00:00Z",
                provider="listen_notes",
                score=1.12,
            )
        ],
    )

    response = client.get("/api/discovery/search/podcasts", params={"q": "ai founder", "limit": 5})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["results"]) == 1
    assert payload["results"][0]["episode_url"] == "https://example.fm/episodes/founder-interview"
    assert payload["results"][0]["provider"] == "listen_notes"


def test_discovery_podcast_search_excludes_existing_user_podcast_sources(
    client, db_session, test_user, monkeypatch
):
    db_session.add(
        UserScraperConfig(
            user_id=test_user.id,
            scraper_type="podcast_rss",
            display_name="Existing Show",
            feed_url="https://Example.FM/rss/",
            config={"feed_url": "https://Example.FM/rss/"},
            is_active=True,
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.routers.api.discovery.search_podcast_episodes",
        lambda query, limit: [
            _build_podcast_hit(
                title="Existing episode",
                episode_url="https://example.fm/episodes/existing",
                feed_url="https://example.fm/rss",
            ),
            _build_podcast_hit(
                title="New episode",
                episode_url="https://new.fm/episodes/new",
                feed_url="https://new.fm/rss",
            ),
        ],
    )

    response = client.get("/api/discovery/search/podcasts", params={"q": "ai founder", "limit": 5})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["results"]) == 1
    assert payload["results"][0]["episode_url"] == "https://new.fm/episodes/new"


def test_discovery_podcast_search_does_not_exclude_other_users_sources(
    client, db_session, test_user, monkeypatch
):
    other_user = User(
        apple_id="other_user_123",
        email="other-user@example.com",
        full_name="Other User",
        is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    db_session.add(
        UserScraperConfig(
            user_id=other_user.id,
            scraper_type="podcast_rss",
            display_name="Other User Show",
            feed_url="https://example.fm/rss",
            config={"feed_url": "https://example.fm/rss"},
            is_active=True,
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.routers.api.discovery.search_podcast_episodes",
        lambda query, limit: [
            _build_podcast_hit(
                title="Episode should remain",
                episode_url="https://example.fm/episodes/keep",
                feed_url="https://example.fm/rss",
            )
        ],
    )

    response = client.get("/api/discovery/search/podcasts", params={"q": "ai founder", "limit": 5})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["results"]) == 1
    assert payload["results"][0]["episode_url"] == "https://example.fm/episodes/keep"


def test_discovery_podcast_search_keeps_results_without_feed_url(
    client, db_session, test_user, monkeypatch
):
    db_session.add(
        UserScraperConfig(
            user_id=test_user.id,
            scraper_type="podcast_rss",
            display_name="Existing Show",
            feed_url="https://example.fm/rss",
            config={"feed_url": "https://example.fm/rss"},
            is_active=True,
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.routers.api.discovery.search_podcast_episodes",
        lambda query, limit: [
            _build_podcast_hit(
                title="Existing feed episode",
                episode_url="https://example.fm/episodes/existing",
                feed_url="https://example.fm/rss",
            ),
            _build_podcast_hit(
                title="Missing feed url episode",
                episode_url="https://unknown.fm/episodes/no-feed",
                feed_url=None,
            ),
        ],
    )

    response = client.get("/api/discovery/search/podcasts", params={"q": "ai founder", "limit": 5})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["results"]) == 1
    assert payload["results"][0]["episode_url"] == "https://unknown.fm/episodes/no-feed"
    assert payload["results"][0]["feed_url"] is None


def test_discovery_podcast_search_applies_limit_after_filtering(
    client, db_session, test_user, monkeypatch
):
    db_session.add_all(
        [
            UserScraperConfig(
                user_id=test_user.id,
                scraper_type="podcast_rss",
                display_name="Existing One",
                feed_url="https://existing-one.fm/rss",
                config={"feed_url": "https://existing-one.fm/rss"},
                is_active=True,
            ),
            UserScraperConfig(
                user_id=test_user.id,
                scraper_type="podcast_rss",
                display_name="Existing Two",
                feed_url="https://existing-two.fm/rss",
                config={"feed_url": "https://existing-two.fm/rss"},
                is_active=True,
            ),
        ]
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.routers.api.discovery.search_podcast_episodes",
        lambda query, limit: [
            _build_podcast_hit(
                title="Filtered one",
                episode_url="https://existing-one.fm/episodes/1",
                feed_url="https://existing-one.fm/rss",
            ),
            _build_podcast_hit(
                title="Kept one",
                episode_url="https://new.fm/episodes/1",
                feed_url="https://new.fm/rss",
            ),
            _build_podcast_hit(
                title="Filtered two",
                episode_url="https://existing-two.fm/episodes/1",
                feed_url="https://existing-two.fm/rss",
            ),
            _build_podcast_hit(
                title="Kept two",
                episode_url="https://new.fm/episodes/2",
                feed_url="https://new.fm/rss",
            ),
            _build_podcast_hit(
                title="Kept three",
                episode_url="https://new.fm/episodes/3",
                feed_url="https://new.fm/rss",
            ),
        ],
    )

    response = client.get("/api/discovery/search/podcasts", params={"q": "ai founder", "limit": 2})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["results"]) == 2
    assert payload["results"][0]["episode_url"] == "https://new.fm/episodes/1"
    assert payload["results"][1]["episode_url"] == "https://new.fm/episodes/2"
