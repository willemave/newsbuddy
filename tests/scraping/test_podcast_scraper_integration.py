from unittest.mock import Mock, patch

import feedparser
import pytest

from app.models.metadata import ContentType
from app.models.schema import Content, ContentStatus
from app.scraping.podcast_unified import PodcastUnifiedScraper
from app.services.queue import TaskType


@pytest.fixture
def mock_podcast_feed():
    """Create a mock podcast RSS feed response."""
    feed = Mock()
    feed.bozo = 0
    feed.feed = {"title": "Test Podcast", "description": "A test podcast", "author": "Test Author"}
    feed.entries = [
        feedparser.FeedParserDict(
            {
                "title": "Episode 1: Introduction",
                "link": "https://example.com/episodes/1",
                "published_parsed": (2023, 1, 1, 0, 0, 0, 0, 0, 0),
                "author": "Host Name",
                "description": "This is the first episode",
                "itunes_episode": "1",
                "itunes_duration": "30:45",
                "enclosures": [
                    {"href": "https://example.com/audio/episode1.mp3", "type": "audio/mpeg"}
                ],
            }
        ),
        feedparser.FeedParserDict(
            {
                "title": "Episode 2: Deep Dive",
                "link": "https://example.com/episodes/2",
                "published_parsed": (2023, 1, 8, 0, 0, 0, 0, 0, 0),
                "description": "This is the second episode",
                "itunes_episode": "2",
                "itunes_duration": "1:15:30",
                "links": [
                    {"href": "https://example.com/audio/episode2.m4a", "type": "audio/x-m4a"}
                ],
            }
        ),
    ]
    return feed


@pytest.fixture
def mock_podcast_config():
    """Create a mock podcast configuration."""
    return {
        "feeds": [{"name": "Test Podcast", "url": "https://example.com/podcast.rss", "limit": 5}]
    }


class TestPodcastScraperIntegration:
    """Test integration between podcast scraper and unified system."""

    @patch("app.scraping.podcast_unified.feedparser.parse")
    @patch("app.scraping.base.get_db")
    @patch("app.scraping.base.get_queue_service")
    def test_podcast_scraper_creates_correct_content(
        self,
        mock_queue_service,
        mock_get_db,
        mock_feedparser,
        mock_podcast_feed,
        mock_podcast_config,
    ):
        """Test that podcast scraper creates correct Content entries."""
        # Setup mocks
        mock_feedparser.return_value = mock_podcast_feed

        # Mock database
        mock_db = Mock()
        mock_get_db.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = (
            None  # No existing content
        )

        # Mock queue service
        queue_service = Mock()
        mock_queue_service.return_value = queue_service

        # Keep track of created content
        created_contents = []

        def mock_add(content):
            created_contents.append(content)
            content.id = len(created_contents)  # Simulate auto-increment ID

        mock_db.add.side_effect = mock_add
        mock_db.refresh.side_effect = lambda x: x

        # Run scraper
        with patch.object(
            PodcastUnifiedScraper,
            "_load_podcast_feeds",
            return_value=mock_podcast_config["feeds"],
        ):
            scraper = PodcastUnifiedScraper()
            saved_count = scraper.run()

        # Verify correct number of items saved
        assert saved_count == 2
        assert len(created_contents) == 2

        # Verify first episode
        episode1 = created_contents[0]
        assert episode1.content_type == ContentType.PODCAST.value
        assert episode1.url == "https://example.com/episodes/1"
        assert episode1.title == "Episode 1: Introduction"
        assert episode1.status == ContentStatus.NEW.value

        # Verify metadata
        metadata1 = episode1.content_metadata
        assert metadata1["audio_url"] == "https://example.com/audio/episode1.mp3"
        assert metadata1["episode_number"] == 1
        assert metadata1["duration_seconds"] == 1845  # 30:45 = 1845 seconds
        assert metadata1["feed_name"] == "Test Podcast"
        assert metadata1["author"] == "Host Name"
        assert metadata1["source"] == "Test Podcast"

        # Verify second episode
        episode2 = created_contents[1]
        assert episode2.content_type == ContentType.PODCAST.value
        assert episode2.url == "https://example.com/episodes/2"
        assert episode2.title == "Episode 2: Deep Dive"
        assert episode2.content_metadata["audio_url"] == "https://example.com/audio/episode2.m4a"
        assert episode2.content_metadata["duration_seconds"] == 4530  # 1:15:30 = 4530 seconds

        # Verify tasks were queued
        assert queue_service.enqueue.call_count == 2
        queue_service.enqueue.assert_any_call(TaskType.PROCESS_CONTENT, content_id=1)
        queue_service.enqueue.assert_any_call(TaskType.PROCESS_CONTENT, content_id=2)

    @patch("app.scraping.podcast_unified.feedparser.parse")
    @patch("app.scraping.base.get_db")
    @patch("app.scraping.base.get_queue_service")
    def test_podcast_scraper_skips_existing_urls(
        self,
        mock_queue_service,
        mock_get_db,
        mock_feedparser,
        mock_podcast_feed,
        mock_podcast_config,
    ):
        """Test that scraper skips URLs that already exist in database."""
        # Setup mocks
        mock_feedparser.return_value = mock_podcast_feed

        # Mock database - first URL exists, second doesn't
        mock_db = Mock()
        mock_get_db.return_value.__enter__.return_value = mock_db
        mock_queue_service.return_value = Mock()

        # Create existing content for first episode
        existing_content = Content()
        existing_content.url = "https://example.com/episodes/1"

        def mock_first(url=None):
            if url == "https://example.com/episodes/1":
                return existing_content
            return None

        mock_db.query.return_value.filter.return_value.first.side_effect = [
            existing_content,  # First episode exists
            None,  # Second episode doesn't exist
        ]

        # Track added content
        added_contents = []
        mock_db.add.side_effect = lambda x: added_contents.append(x)

        # Run scraper
        with patch.object(
            PodcastUnifiedScraper,
            "_load_podcast_feeds",
            return_value=mock_podcast_config["feeds"],
        ):
            scraper = PodcastUnifiedScraper()
            saved_count = scraper.run()

        # Verify only one new item was saved
        assert saved_count == 1
        assert len(added_contents) == 1
        assert added_contents[0].url == "https://example.com/episodes/2"

    @patch("app.scraping.podcast_unified.feedparser.parse")
    def test_podcast_scraper_summarizes_missing_audio_entries(
        self,
        mock_feedparser,
        caplog,
    ):
        """Entries without audio should be summarized once per feed."""
        mock_feedparser.return_value = Mock(
            bozo=0,
            feed={"title": "Test Podcast"},
            entries=[
                feedparser.FeedParserDict({"title": "No Audio 1", "link": "https://example.com/1"}),
                feedparser.FeedParserDict({"title": "No Audio 2", "link": "https://example.com/2"}),
            ],
        )

        with patch.object(
            PodcastUnifiedScraper,
            "_load_podcast_feeds",
            return_value=[
                {
                    "name": "Test Podcast",
                    "url": "https://example.com/feed.xml",
                    "limit": 5,
                }
            ],
        ):
            scraper = PodcastUnifiedScraper()
            with caplog.at_level("INFO"):
                items = scraper.scrape()

        assert items == []
        assert any(
            "Skipped 2 podcast entries without audio enclosures from Test Podcast" in message
            for message in caplog.messages
        )
        assert not any("No audio enclosure found for:" in message for message in caplog.messages)

    def test_podcast_feed_parsing_edge_cases(self):
        """Test edge cases in podcast feed parsing."""
        scraper = PodcastUnifiedScraper()

        # Test duration parsing
        assert scraper._parse_duration("1:23:45") == 5025  # 1h 23m 45s
        assert scraper._parse_duration("45:30") == 2730  # 45m 30s
        assert scraper._parse_duration("180") == 180  # 3 minutes
        assert scraper._parse_duration("invalid") is None

        # Test finding audio enclosure with no enclosures
        entry_no_audio = feedparser.FeedParserDict(
            {
                "title": "No Audio Episode",
                "link": "https://example.com/episodes/3",
                "description": "An episode with no audio",
            }
        )
        assert scraper._find_audio_enclosure(entry_no_audio, "No Audio") is None

        # Test finding audio by file extension in links
        entry_with_link = feedparser.FeedParserDict(
            {
                "title": "Link Audio Episode",
                "link": "https://example.com/episodes/4",
                "links": [
                    {"href": "https://example.com/page.html", "type": "text/html"},
                    {"href": "https://example.com/audio/episode4.mp3", "type": ""},
                ],
            }
        )
        audio_url = scraper._find_audio_enclosure(entry_with_link, "Link Audio")
        assert audio_url == "https://example.com/audio/episode4.mp3"


class TestPodcastProcessingFlow:
    """Test the complete flow from scraping to processing."""

    @patch("app.pipeline.worker.get_db")
    @patch("app.pipeline.worker.get_task_queue_gateway")
    def test_process_content_queues_process_podcast_media(self, mock_queue_gateway, mock_get_db):
        """Test that PROCESS_CONTENT for podcast queues PROCESS_PODCAST_MEDIA."""
        # Create mock podcast content
        mock_content = Content()
        mock_content.id = 100
        mock_content.content_type = ContentType.PODCAST.value
        mock_content.url = "https://example.com/episodes/1"
        mock_content.content_metadata = {"audio_url": "https://example.com/audio/episode1.mp3"}
        mock_content.status = ContentStatus.NEW.value

        # Mock database
        mock_db = Mock()
        mock_get_db.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = mock_content

        # Mock queue gateway
        queue_gateway = Mock()
        mock_queue_gateway.return_value = queue_gateway

        # Import and create worker
        from app.pipeline.worker import ContentWorker

        worker = ContentWorker()

        # Process content
        success = worker.process_content(100, "test-worker")

        # Verify success
        assert success is True

        # Verify PROCESS_PODCAST_MEDIA task was queued
        queue_gateway.enqueue.assert_called_once_with(
            TaskType.PROCESS_PODCAST_MEDIA, content_id=100
        )
