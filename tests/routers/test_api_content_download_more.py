from app.models.schema import UserScraperConfig
from app.routers.api import content_actions
from app.services.feed_backfill import FeedBackfillResult


def test_download_more_from_series_success(
    client,
    content_factory,
    db_session,
    status_entry_factory,
    test_user,
    monkeypatch,
):
    config = UserScraperConfig(
        user_id=test_user.id,
        scraper_type="atom",
        display_name="Example Feed",
        feed_url="https://example.com/feed.xml",
        config={"feed_url": "https://example.com/feed.xml", "limit": 1},
        is_active=True,
    )
    db_session.add(config)
    db_session.commit()
    db_session.refresh(config)

    content = content_factory(
        content_type="article",
        url="https://example.com/post-1",
        source_url="https://example.com/post-1",
        title="Example Post",
        source="Example Feed",
        status="completed",
        content_metadata={
            "feed_config_id": config.id,
            "feed_url": "https://example.com/feed.xml",
            "source": "Example Feed",
        },
    )
    status_entry_factory(user=test_user, content=content, status="inbox")

    async def _run_in_threadpool(func, *args, **kwargs):
        return func(*args, **kwargs)

    def _fake_backfill(_request):
        return FeedBackfillResult(
            config_id=config.id,
            base_limit=1,
            target_limit=6,
            scraped=6,
            saved=5,
            duplicates=1,
            errors=0,
        )

    monkeypatch.setattr(content_actions, "run_in_threadpool", _run_in_threadpool)
    monkeypatch.setattr(content_actions, "backfill_feed_for_config", _fake_backfill)

    response = client.post(
        f"/api/content/{content.id}/download-more",
        json={"count": 5},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["requested_count"] == 5
    assert data["base_limit"] == 1
    assert data["target_limit"] == 6
    assert data["saved"] == 5
