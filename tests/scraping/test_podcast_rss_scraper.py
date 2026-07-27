from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

from app.constants import AGGREGATOR_FEED_URL_PREFIX, AGGREGATOR_SCRAPER_TYPE
from app.models.contracts import ContentStatus, ContentType, TaskType
from app.models.db import NewsItem, NewsItemDiscussion, UserScraperConfig
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
    existing.url = "https://example.com/ep1"
    existing.content_type = ContentType.PODCAST.value

    mock_db = Mock()
    mock_db.query.return_value.filter.return_value.all.return_value = [existing]

    @contextmanager
    def _db_context():
        yield mock_db

    with (
        patch("app.scraping.base.get_db", lambda: _db_context()),
        patch("app.scraping.base.get_queue_service", return_value=Mock()),
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
        patch("app.scraping.base.upsert_news_items", return_value=[(existing, False)]),
        patch("app.scraping.base.sync_news_item_discussions_from_news_items", return_value=[None]),
        patch("app.scraping.base.news_item_discussion_refresh_ids", return_value=set()),
    ):
        scraper = PodcastUnifiedScraper()
        stats = scraper._save_items_with_stats([news_item])

    assert stats["duplicates"] == 1
    queue_service.enqueue_many_in_session.assert_not_called()


def test_existing_visible_due_news_enqueues_discussion_refresh(
    db_session,
    user_factory,
):
    """Duplicate HN/Reddit scrapes should enqueue refresh once a visible row is due."""
    user = user_factory()
    assert user.id is not None
    db_session.add(
        UserScraperConfig(
            user_id=user.id,
            scraper_type=AGGREGATOR_SCRAPER_TYPE,
            display_name="hackernews",
            feed_url=f"{AGGREGATOR_FEED_URL_PREFIX}hackernews",
            config={"key": "hackernews"},
            is_active=True,
        )
    )
    existing = NewsItem(
        ingest_key="existing-hn-due",
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="12345",
        canonical_item_url="https://news.ycombinator.com/item?id=12345",
        canonical_story_url="https://example.com/story",
        article_url="https://example.com/story",
        article_title="Example story",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=12345",
        raw_metadata={
            "platform": "hackernews",
            "aggregator": {
                "external_id": "12345",
                "metadata": {"comments_count": 1},
            },
        },
        status="ready",
    )
    db_session.add(existing)
    db_session.flush()
    db_session.add(
        NewsItemDiscussion(
            news_item_id=existing.id,
            platform="hackernews",
            external_id="12345",
            discussion_url="https://news.ycombinator.com/item?id=12345",
            comment_count=1,
            fetched_comment_count=1,
            summary={"overview": "old"},
            summary_status="completed",
            raw_comments_ref={"kind": "storage"},
            last_refresh_status="completed",
            next_refresh_after=None,
        )
    )
    db_session.commit()

    queue_service = Mock()

    @contextmanager
    def _db_context():
        yield db_session

    news_item = {
        "url": "https://example.com/story",
        "title": "Example story",
        "content_type": ContentType.NEWS,
        "metadata": {
            "platform": "hackernews",
            "source": "Hacker News",
            "source_type": "hackernews",
            "source_label": "Hacker News",
            "article": {
                "url": "https://example.com/story",
                "title": "Example story",
                "source_domain": "example.com",
            },
            "aggregator": {
                "name": "Hacker News",
                "external_id": "12345",
                "metadata": {"comments_count": 1},
            },
            "discussion_url": "https://news.ycombinator.com/item?id=12345",
        },
    }

    with (
        patch("app.scraping.base.get_db", lambda: _db_context()),
        patch("app.scraping.base.get_queue_service", return_value=queue_service),
    ):
        scraper = PodcastUnifiedScraper()
        stats = scraper._save_items_with_stats([news_item])

    assert stats["duplicates"] == 1
    queue_service.enqueue_many_in_session.assert_called_once()
    queued_requests = queue_service.enqueue_many_in_session.call_args.args[1]
    assert len(queued_requests) == 1
    assert queued_requests[0].task_type == TaskType.FETCH_NEWS_ITEM_DISCUSSION
    assert queued_requests[0].payload == {"news_item_id": existing.id}


def test_new_visible_supported_news_item_enqueues_discussion_refresh(db_session, user_factory):
    """New HN/Reddit news items should queue discussion refresh only when visible."""
    user = user_factory()
    assert user.id is not None
    reddit_config = UserScraperConfig(
        user_id=user.id,
        scraper_type="reddit",
        display_name="example",
        feed_url="https://www.reddit.com/r/example/",
        config={"subreddit": "example", "limit": 1},
        is_active=True,
    )
    db_session.add(reddit_config)
    db_session.commit()
    queue_service = Mock()

    @contextmanager
    def _db_context():
        yield db_session

    news_item = {
        "url": "https://example.com/story",
        "title": "Example story",
        "content_type": ContentType.NEWS,
        "visibility_scope": "user",
        "owner_user_id": user.id,
        "user_scraper_config_id": reddit_config.id,
        "metadata": {
            "platform": "reddit",
            "source": "example",
            "source_type": "reddit",
            "source_label": "example",
            "summary": {
                "title": "Example story",
                "summary": "Example story summary",
                "key_points": ["Example story point"],
            },
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
    ):
        scraper = PodcastUnifiedScraper()
        stats = scraper._save_items_with_stats([news_item])

    assert stats["saved"] == 1
    saved_item = db_session.query(NewsItem).filter(NewsItem.source_external_id == "abc123").one()
    queue_service.enqueue_many_in_session.assert_called_once()
    queued_requests = queue_service.enqueue_many_in_session.call_args.args[1]
    assert len(queued_requests) == 1
    assert queued_requests[0].task_type == TaskType.FETCH_NEWS_ITEM_DISCUSSION
    assert queued_requests[0].payload == {"news_item_id": saved_item.id}
