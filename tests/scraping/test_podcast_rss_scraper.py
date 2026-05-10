from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

from app.models.contracts import ContentStatus, ContentType, TaskType
from app.models.db import NewsItem
from app.scraping.podcast_unified import PodcastUnifiedScraper


def _build_item(url: str, user_id: int | None = None) -> dict:
    return {
        "url": url,
        "title": "Test Episode",
        "content_type": ContentType.PODCAST,
        "metadata": {"source": "Test Podcast", "platform": "podcast"},
        "user_id": user_id,
    }


@pytest.mark.parametrize(
    "status",
    [
        ContentStatus.NEW.value,
        ContentStatus.PENDING.value,
        ContentStatus.PROCESSING.value,
        ContentStatus.FAILED.value,
        ContentStatus.SKIPPED.value,
    ],
)
def test_existing_podcast_entries_are_skipped(status):
    """Ensure duplicate podcast items do not create new records regardless of status."""
    existing = Mock()
    existing.id = 123
    existing.status = status

    mock_db = Mock()
    mock_db.query.return_value.filter.return_value.first.return_value = existing

    @contextmanager
    def _db_context():
        yield mock_db

    with (
        patch("app.scraping.base.get_db", lambda: _db_context()),
        patch("app.scraping.base.get_queue_service", return_value=Mock()),
        patch("app.scraping.base.ensure_inbox_status", return_value=False),
    ):
        scraper = PodcastUnifiedScraper()
        stats = scraper._save_items_with_stats([_build_item("https://example.com/ep1")])

    assert stats["duplicates"] == 1
    mock_db.add.assert_not_called()


def test_existing_news_entries_are_not_reenqueued():
    """Duplicate news rows should not enqueue enrichment again just because they are not ready."""
    existing = NewsItem(
        id=321,
        ingest_key="existing-news",
        visibility_scope="global",
        platform="reddit",
        source_type="reddit",
        source_label="example",
        source_external_id="abc123",
        canonical_item_url="https://reddit.com/r/example/comments/abc123/example_story/",
        canonical_story_url="https://example.com/story",
        article_url="https://example.com/story",
        article_title="Example story",
        discussion_url="https://reddit.com/r/example/comments/abc123/example_story/",
        raw_metadata={},
        status="new",
    )

    mock_db = Mock()
    query = mock_db.query.return_value
    query.filter.return_value.order_by.return_value.first.return_value = existing
    queue_service = Mock()

    @contextmanager
    def _db_context():
        yield mock_db

    news_item = {
        "url": "https://example.com/story",
        "title": "Example story",
        "content_type": ContentType.NEWS,
        "metadata": {
            "platform": "reddit",
            "source": "example",
            "source_type": "reddit",
            "source_label": "example",
            "article": {
                "url": "https://example.com/story",
                "title": "Example story",
                "source_domain": "example.com",
            },
            "aggregator": {
                "name": "Reddit",
                "external_id": "abc123",
            },
            "discussion_url": "https://reddit.com/r/example/comments/abc123/example_story/",
        },
    }

    with (
        patch("app.scraping.base.get_db", lambda: _db_context()),
        patch("app.scraping.base.get_queue_service", return_value=queue_service),
        patch("app.scraping.base.ensure_inbox_status", return_value=False),
    ):
        scraper = PodcastUnifiedScraper()
        stats = scraper._save_items_with_stats([news_item])

    assert stats["duplicates"] == 1
    queue_service.enqueue.assert_not_called()


def test_new_supported_news_item_enqueues_discussion_refresh(db_session):
    """New HN/Reddit news items should queue the news-native discussion pipeline."""
    queue_service = Mock()

    @contextmanager
    def _db_context():
        yield db_session

    news_item = {
        "url": "https://example.com/story",
        "title": "Example story",
        "content_type": ContentType.NEWS,
        "metadata": {
            "platform": "reddit",
            "source": "example",
            "source_type": "reddit",
            "source_label": "example",
            "article": {
                "url": "https://example.com/story",
                "title": "Example story",
                "source_domain": "example.com",
            },
            "aggregator": {
                "name": "Reddit",
                "external_id": "abc123",
                "metadata": {"comments_count": 4},
            },
            "discussion_url": "https://reddit.com/r/example/comments/abc123/example_story/",
        },
    }

    with (
        patch("app.scraping.base.get_db", lambda: _db_context()),
        patch("app.scraping.base.get_queue_service", return_value=queue_service),
        patch("app.scraping.base.ensure_inbox_status", return_value=False),
    ):
        scraper = PodcastUnifiedScraper()
        stats = scraper._save_items_with_stats([news_item])

    assert stats["saved"] == 1
    saved_item = db_session.query(NewsItem).filter(NewsItem.source_external_id == "abc123").one()
    queue_service.enqueue.assert_any_call(
        TaskType.FETCH_NEWS_ITEM_DISCUSSION,
        payload={"news_item_id": saved_item.id},
    )
    queue_service.enqueue.assert_any_call(
        TaskType.ENRICH_NEWS_ITEM_ARTICLE,
        payload={"news_item_id": saved_item.id},
        dedupe=False,
    )
