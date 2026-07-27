from unittest.mock import MagicMock, patch

import feedparser
import pytest

from app.models.contracts import ContentStatus, ContentType, TaskType
from app.models.db import Content
from app.scraping.substack_unified import SubstackScraper

# Sample feedparser entry
mock_entry_article = {
    "title": "Test Article",
    "link": "http://test.com/article",
    "author": "Test Author",
    "published_parsed": (2025, 6, 7, 12, 0, 0, 5, 158, 0),
    "content": [{"type": "text/html", "value": "<p>Test Content</p>"}],
    "id": "test-entry-123",
}

mock_entry_podcast = {
    "title": "My Test Podcast Episode",
    "link": "http://podcast.com/episode",
    "author": "Podcast Host",
    "published_parsed": (2025, 6, 7, 13, 0, 0, 5, 158, 0),
    "summary": "A summary of the podcast.",
    "id": "podcast-entry-456",
}


@pytest.fixture
def mock_db_session():
    """Fixture for a mocked database session."""
    with patch("app.scraping.base.get_db") as mock_get_db:
        mock_session = MagicMock()
        # Configure query chains to simulate no existing data
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_session.query.return_value.filter.return_value.all.return_value = []
        mock_session.add.side_effect = lambda content: setattr(content, "id", 1)
        mock_get_db.return_value.__enter__.return_value = mock_session
        yield mock_session


@pytest.fixture
def mock_queue_service():
    """Fixture for mocked queue service."""
    with patch("app.scraping.base.get_queue_service") as mock_get_queue:
        mock_service = MagicMock()
        mock_get_queue.return_value = mock_service
        yield mock_service


@patch("app.scraping.substack_unified.fetch_and_parse_feed")
def test_scrape_process_and_filter(mock_feedparser_parse, mock_db_session, mock_queue_service):
    """Test the full scrape and process flow, including filtering."""
    # Mock feedparser results
    mock_feed_result = MagicMock()
    mock_feed_result.bozo = 0
    mock_feed_result.entries = [mock_entry_article]
    # Add feed metadata
    mock_feed_result.feed = {"title": "Test Feed", "description": "A test feed for testing"}
    mock_feedparser_parse.return_value = mock_feed_result

    # Create scraper with mocked feeds
    with patch.object(
        SubstackScraper,
        "_load_feeds",
        return_value=[{"url": "http://test.com/feed", "name": "Test Feed", "limit": 10}],
    ):
        scraper = SubstackScraper()
        items = scraper.scrape()

        # Verify one item was processed
        assert len(items) == 1

        item = items[0]
        assert item["url"] == "https://test.com/article"  # Note: normalized to https
        assert item["title"] == "Test Article"
        assert item["content_type"] == ContentType.ARTICLE
        assert item["metadata"]["source"] == "Test Feed"
        assert item["metadata"]["source_domain"] == "test.com"
        assert item["metadata"]["feed_name"] == "Test Feed"
        assert item["metadata"]["author"] == "Test Author"


@patch("app.scraping.substack_unified.fetch_and_parse_feed")
def test_scrape_filters_podcasts(mock_feedparser_parse, mock_db_session, mock_queue_service):
    """Test that podcast entries are filtered out."""
    # Mock feedparser results with podcast entry
    mock_feed_result = MagicMock()
    mock_feed_result.bozo = 0
    mock_feed_result.entries = [mock_entry_podcast]
    mock_feed_result.feed = {"title": "Test Feed", "description": "A test feed"}
    mock_feedparser_parse.return_value = mock_feed_result

    # Create scraper with mocked feeds
    with patch.object(
        SubstackScraper,
        "_load_feeds",
        return_value=[{"url": "http://test.com/feed", "name": "Test Feed", "limit": 10}],
    ):
        scraper = SubstackScraper()
        items = scraper.scrape()

        # Verify podcast was filtered out
        assert len(items) == 0


@patch("app.scraping.substack_unified.fetch_and_parse_feed")
def test_scrape_skips_logging_for_encoding_override(
    mock_feedparser_parse, mock_db_session, mock_queue_service
):
    """Verify CharacterEncodingOverride is treated as non-critical and not logged as an error."""
    mock_feed_result = MagicMock()
    mock_feed_result.bozo = 1
    mock_feed_result.bozo_exception = feedparser.exceptions.CharacterEncodingOverride(
        "document declared as us-ascii, but parsed as ISO-8859-1"
    )
    mock_feed_result.entries = []
    mock_feed_result.feed = {
        "title": "Encoding Test Feed",
        "description": "Feed with encoding mismatch",
    }
    mock_feedparser_parse.return_value = mock_feed_result

    with (
        patch.object(
            SubstackScraper,
            "_load_feeds",
            return_value=[{"url": "http://test.com/feed", "name": "Encoding Feed", "limit": 10}],
        ),
        patch("app.scraping.substack_unified.logger.warning") as warning,
        patch("app.scraping.substack_unified.logger.exception") as exception,
    ):
        scraper = SubstackScraper()
        scraper.scrape()

        warning.assert_not_called()
        exception.assert_not_called()


@patch("app.scraping.substack_unified.fetch_and_parse_feed")
def test_scrape_handles_missing_link(mock_feedparser_parse, mock_db_session, mock_queue_service):
    """Test handling of entries without links."""
    # Mock entry without link
    bad_entry = {
        "title": "Entry Without Link",
        "author": "Test Author",
        "published_parsed": (2025, 6, 7, 12, 0, 0, 5, 158, 0),
        "content": [{"type": "text/html", "value": "<p>Content</p>"}],
    }

    mock_feed_result = MagicMock()
    mock_feed_result.bozo = 0
    mock_feed_result.entries = [bad_entry]
    mock_feed_result.feed = {"title": "Test Feed", "description": "A test feed"}
    mock_feedparser_parse.return_value = mock_feed_result

    # Create scraper with mocked feeds
    with patch.object(
        SubstackScraper,
        "_load_feeds",
        return_value=[{"url": "http://test.com/feed", "name": "Test Feed", "limit": 10}],
    ):
        scraper = SubstackScraper()
        items = scraper.scrape()

        # Verify entry was skipped
        assert len(items) == 0


def test_run_saves_to_database(mock_db_session, mock_queue_service):
    """Test that run() saves items to database and queues tasks."""
    with patch("app.scraping.substack_unified.fetch_and_parse_feed") as mock_feedparser_parse:
        # Mock feedparser results
        mock_feed_result = MagicMock()
        mock_feed_result.bozo = 0
        mock_feed_result.entries = [mock_entry_article]
        mock_feed_result.feed = {"title": "Test Feed", "description": "A test feed"}
        mock_feedparser_parse.return_value = mock_feed_result

        # Create scraper with mocked feeds
        with patch.object(
            SubstackScraper,
            "_load_feeds",
            return_value=[{"url": "http://test.com/feed", "name": "Test Feed", "limit": 10}],
        ):
            scraper = SubstackScraper()
            saved_count = scraper.run()

            # Verify item was saved
            assert saved_count == 1

            # Verify database operations
            mock_db_session.add.assert_called_once()
            mock_db_session.flush.assert_called_once()
            mock_db_session.commit.assert_not_called()
            mock_db_session.refresh.assert_not_called()

            # Verify the created content object
            created_content = mock_db_session.add.call_args[0][0]
            assert isinstance(created_content, Content)
            assert created_content.content_type == ContentType.ARTICLE.value
            assert created_content.url == "https://test.com/article"
            assert created_content.title == "Test Article"
            assert created_content.source == "Test Feed"  # Verify source field is set
            assert created_content.status == ContentStatus.NEW.value

            # Verify task was queued
            mock_queue_service.enqueue_many_in_session.assert_called_once()
            queued_requests = mock_queue_service.enqueue_many_in_session.call_args.args[1]
            assert len(queued_requests) == 1
            assert queued_requests[0].task_type == TaskType.PROCESS_CONTENT
            assert queued_requests[0].content_id == 1


def test_run_skips_existing_urls(mock_db_session, mock_queue_service):
    """Test that run() skips URLs that already exist."""
    with patch("app.scraping.substack_unified.fetch_and_parse_feed") as mock_feedparser_parse:
        # Mock feedparser results
        mock_feed_result = MagicMock()
        mock_feed_result.bozo = 0
        mock_feed_result.entries = [mock_entry_article]
        mock_feed_result.feed = {"title": "Test Feed", "description": "A test feed"}
        mock_feedparser_parse.return_value = mock_feed_result

        # Mock existing content in database
        existing_content = Content()
        existing_content.url = "https://test.com/article"
        existing_content.content_type = ContentType.ARTICLE.value
        mock_db_session.query.return_value.filter.return_value.all.return_value = [existing_content]

        # Create scraper with mocked feeds
        with patch.object(
            SubstackScraper,
            "_load_feeds",
            return_value=[{"url": "http://test.com/feed", "name": "Test Feed", "limit": 10}],
        ):
            scraper = SubstackScraper()
            saved_count = scraper.run()

            # Verify no new items were saved
            assert saved_count == 0
            mock_db_session.add.assert_not_called()
            mock_queue_service.enqueue_many_in_session.assert_not_called()


def test_url_normalization():
    """Test URL normalization functionality."""
    scraper = SubstackScraper()

    # Test http to https conversion
    assert scraper._normalize_url("http://test.com/article") == "https://test.com/article"

    # Test trailing slash removal
    assert scraper._normalize_url("https://test.com/article/") == "https://test.com/article"

    # Test combined normalization
    assert scraper._normalize_url("http://test.com/article/") == "https://test.com/article"


@patch("app.scraping.substack_unified.fetch_and_parse_feed")
def test_scrape_respects_limit(mock_feedparser_parse, mock_db_session, mock_queue_service):
    """Test that scraper respects the limit configuration."""
    # Create multiple entries
    entries = []
    for i in range(5):
        entries.append(
            {
                "title": f"Test Article {i}",
                "link": f"http://test.com/article{i}",
                "author": "Test Author",
                "published_parsed": (2025, 6, 7, 12, 0, 0, 5, 158, 0),
                "content": [{"type": "text/html", "value": f"<p>Content {i}</p>"}],
                "id": f"test-entry-{i}",
            }
        )

    # Mock feedparser results
    mock_feed_result = MagicMock()
    mock_feed_result.bozo = 0
    mock_feed_result.entries = entries
    mock_feed_result.feed = {"title": "Test Feed", "description": "A test feed for testing"}
    mock_feedparser_parse.return_value = mock_feed_result

    # Create scraper with limit of 2
    with patch.object(
        SubstackScraper,
        "_load_feeds",
        return_value=[{"url": "http://test.com/feed", "name": "Test Feed", "limit": 2}],
    ):
        scraper = SubstackScraper()
        items = scraper.scrape()

        # Verify only 2 items were processed despite having 5 entries
        assert len(items) == 2
        assert items[0]["title"] == "Test Article 0"
        assert items[1]["title"] == "Test Article 1"


def test_no_feeds_configured():
    """Test behavior when no feeds are configured."""
    with patch.object(SubstackScraper, "_load_feeds", return_value=[]):
        scraper = SubstackScraper()
        items = scraper.scrape()

        assert len(items) == 0
