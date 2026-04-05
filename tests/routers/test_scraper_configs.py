from datetime import UTC, datetime

import pytest

from app.constants import DEFAULT_NEW_FEED_LIMIT
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import (
    Content,
    ContentReadStatus,
    ContentStatusEntry,
    ProcessingTask,
    UserScraperConfig,
)


@pytest.fixture(autouse=True)
def _stub_feed_validation(monkeypatch):
    def _validate(feed_url: str):
        return {"feed_url": feed_url.strip()}

    monkeypatch.setattr(
        "app.models.internal.scraper_configs.FEED_VALIDATOR.validate_feed_url",
        _validate,
    )


def _add_inbox_status(db_session, user_id: int, content_id: int) -> None:
    db_session.add(
        ContentStatusEntry(
            user_id=user_id,
            content_id=content_id,
            status="inbox",
        )
    )


def _add_active_task(db_session, *, content_id: int) -> None:
    db_session.add(
        ProcessingTask(
            task_type="process_content",
            content_id=content_id,
            status="pending",
            queue_name="content",
            payload={},
        )
    )


def test_scraper_configs_crud(client, db_session, test_user):
    create_payload = {
        "scraper_type": "substack",
        "display_name": "My Substack",
        "config": {"feed_url": "https://example.com/feed"},
        "is_active": True,
    }
    create_resp = client.post("/api/scrapers", json=create_payload)
    assert create_resp.status_code == 201
    created = create_resp.json()
    config_id = created["id"]
    assert created["scraper_type"] == "substack"
    assert created["feed_url"] == "https://example.com/feed"
    assert created["limit"] == DEFAULT_NEW_FEED_LIMIT

    list_resp = client.get("/api/scrapers")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert len(data) == 1
    assert data[0]["config"]["feed_url"] == "https://example.com/feed"
    assert data[0]["feed_url"] == "https://example.com/feed"

    update_resp = client.put(f"/api/scrapers/{config_id}", json={"is_active": False})
    assert update_resp.status_code == 200
    assert update_resp.json()["is_active"] is False

    delete_resp = client.delete(f"/api/scrapers/{config_id}")
    assert delete_resp.status_code == 204

    db_session.expire_all()
    remaining = db_session.query(UserScraperConfig).all()
    assert remaining == []


def test_scraper_configs_filtering_and_limits(client, test_user):
    # Create a feed and a podcast config
    client.post(
        "/api/scrapers",
        json={
            "scraper_type": "substack",
            "display_name": "My Substack",
            "config": {"feed_url": "https://example.com/feed"},
            "is_active": True,
        },
    )
    podcast_resp = client.post(
        "/api/scrapers",
        json={
            "scraper_type": "podcast_rss",
            "display_name": "My Podcast",
            "config": {"feed_url": "https://pod.example.com/rss", "limit": 15},
            "is_active": True,
        },
    )
    assert podcast_resp.status_code == 201
    podcast_data = podcast_resp.json()
    assert podcast_data["limit"] == 15

    # Filter by type
    type_resp = client.get("/api/scrapers?type=podcast_rss")
    assert type_resp.status_code == 200
    filtered = type_resp.json()
    assert len(filtered) == 1
    assert filtered[0]["scraper_type"] == "podcast_rss"
    assert filtered[0]["feed_url"] == "https://pod.example.com/rss"
    assert filtered[0]["limit"] == 15

    # Multiple types
    multi_resp = client.get("/api/scrapers?types=podcast_rss,atom")
    assert multi_resp.status_code == 200
    assert len(multi_resp.json()) == 1

    # Invalid type
    bad_resp = client.get("/api/scrapers?type=invalid_type")
    assert bad_resp.status_code == 400
    assert "Unsupported scraper types" in bad_resp.json()["detail"]


def test_scraper_config_limit_validation(client, test_user):
    bad_resp = client.post(
        "/api/scrapers",
        json={
            "scraper_type": "podcast_rss",
            "display_name": "Bad Limit",
            "config": {"feed_url": "https://pod.example.com/rss", "limit": 0},
            "is_active": True,
        },
    )
    # Pydantic validation errors return 422
    assert bad_resp.status_code == 422

    ok_resp = client.post(
        "/api/scrapers",
        json={
            "scraper_type": "podcast_rss",
            "display_name": "Good Limit",
            "config": {"feed_url": "https://pod.example.com/rss", "limit": 25},
            "is_active": True,
        },
    )
    assert ok_resp.status_code == 201
    assert ok_resp.json()["limit"] == 25


def test_subscribe_feed_defaults_limit(client, test_user):
    resp = client.post(
        "/api/scrapers/subscribe",
        json={
            "feed_url": "https://example.com/feed.xml",
            "feed_type": "atom",
            "display_name": "Example Feed",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["limit"] == DEFAULT_NEW_FEED_LIMIT


def test_scraper_config_reddit(client, test_user):
    resp = client.post(
        "/api/scrapers",
        json={
            "scraper_type": "reddit",
            "display_name": "Machine Learning",
            "config": {"subreddit": "MachineLearning", "limit": 5},
            "is_active": True,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["scraper_type"] == "reddit"
    assert data["feed_url"] == "https://www.reddit.com/r/MachineLearning/"
    assert data["config"]["subreddit"] == "MachineLearning"


def test_scraper_config_list_includes_derived_stats(client, db_session, test_user):
    article_config = client.post(
        "/api/scrapers",
        json={
            "scraper_type": "substack",
            "display_name": "Import AI",
            "config": {"feed_url": "https://importai.substack.com/feed"},
            "is_active": True,
        },
    ).json()
    podcast_config = client.post(
        "/api/scrapers",
        json={
            "scraper_type": "podcast_rss",
            "display_name": "AI Radio",
            "config": {"feed_url": "https://pod.example.com/rss", "limit": 10},
            "is_active": True,
        },
    ).json()
    youtube_config = client.post(
        "/api/scrapers",
        json={
            "scraper_type": "youtube",
            "display_name": "AI Channel",
            "config": {"feed_url": "https://www.youtube.com/channel/UC123"},
            "is_active": True,
        },
    ).json()

    article_old = Content(
        url="https://importai.substack.com/p/older",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        processed_at=datetime(2026, 3, 27, 9, 0, 0),
        publication_date=datetime(2026, 3, 27, 8, 0, 0),
        content_metadata={
            "feed_config_id": article_config["id"],
            "feed_url": "https://importai.substack.com/feed",
        },
    )
    article_new = Content(
        url="https://importai.substack.com/p/newer",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        processed_at=datetime(2026, 3, 30, 9, 0, 0),
        publication_date=datetime(2026, 3, 30, 8, 0, 0),
        content_metadata={
            "feed_config_id": article_config["id"],
            "feed_url": "https://importai.substack.com/feed",
        },
    )
    podcast_completed = Content(
        url="https://pod.example.com/episodes/1",
        content_type=ContentType.PODCAST.value,
        status=ContentStatus.COMPLETED.value,
        processed_at=datetime(2026, 3, 29, 7, 0, 0),
        publication_date=datetime(2026, 3, 29, 6, 30, 0),
        content_metadata={
            "feed_config_id": podcast_config["id"],
            "feed_url": "https://pod.example.com/rss",
        },
    )
    podcast_pending = Content(
        url="https://pod.example.com/episodes/2",
        content_type=ContentType.PODCAST.value,
        status=ContentStatus.PENDING.value,
        publication_date=datetime(2026, 3, 31, 6, 30, 0),
        content_metadata={
            "feed_config_id": podcast_config["id"],
            "feed_url": "https://pod.example.com/rss",
        },
    )
    unrelated = Content(
        url="https://example.com/other",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        processed_at=datetime.now(UTC).replace(tzinfo=None),
        publication_date=datetime.now(UTC).replace(tzinfo=None),
        content_metadata={"feed_url": "https://other.example.com/feed"},
    )

    db_session.add_all([article_old, article_new, podcast_completed, podcast_pending, unrelated])
    db_session.commit()
    for content in [article_old, article_new, podcast_completed, podcast_pending, unrelated]:
        db_session.refresh(content)

    for content in [article_old, article_new, podcast_completed, podcast_pending, unrelated]:
        _add_inbox_status(db_session, test_user.id, content.id)
    _add_active_task(db_session, content_id=podcast_pending.id)
    db_session.add(ContentReadStatus(user_id=test_user.id, content_id=article_old.id))
    db_session.commit()

    response = client.get("/api/scrapers?types=substack,podcast_rss,youtube")
    assert response.status_code == 200
    payload = {item["id"]: item for item in response.json()}

    article_stats = payload[article_config["id"]]["stats"]
    assert article_stats["total_count"] == 2
    assert article_stats["completed_count"] == 2
    assert article_stats["unread_count"] == 1
    assert article_stats["processing_count"] == 0
    assert article_stats["latest_processed_at"].startswith("2026-03-30T09:00:00")
    assert article_stats["latest_publication_at"].startswith("2026-03-30T08:00:00")
    assert article_stats["next_expected_at"].startswith("2026-04-02T08:00:00")
    assert article_stats["interval_sample_size"] == 1

    podcast_stats = payload[podcast_config["id"]]["stats"]
    assert podcast_stats["total_count"] == 2
    assert podcast_stats["completed_count"] == 1
    assert podcast_stats["unread_count"] == 1
    assert podcast_stats["processing_count"] == 1
    assert podcast_stats["latest_processed_at"].startswith("2026-03-29T07:00:00")
    assert podcast_stats["latest_publication_at"].startswith("2026-03-31T06:30:00")
    assert podcast_stats["next_expected_at"].startswith("2026-04-02T06:30:00")
    assert podcast_stats["interval_sample_size"] == 1

    youtube_stats = payload[youtube_config["id"]]["stats"]
    assert youtube_stats["total_count"] == 0
    assert youtube_stats["completed_count"] == 0
    assert youtube_stats["unread_count"] == 0
    assert youtube_stats["processing_count"] == 0
    assert youtube_stats["latest_processed_at"] is None
    assert youtube_stats["next_expected_at"] is None
