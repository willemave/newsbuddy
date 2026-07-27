"""
Tests for podcast audio URL extraction and download functionality.
"""

from unittest.mock import Mock, patch

import feedparser

from app.models.contracts import ContentStatus, ContentType
from app.models.domain.content import ContentData
from app.pipeline.podcast_workers import PodcastMediaWorker
from app.scraping.podcast_unified import PodcastUnifiedScraper


class TestPodcastAudioExtraction:
    """Test podcast RSS parsing and audio URL extraction."""

    def test_find_audio_enclosure_from_enclosures(self):
        """Test extracting audio URL from enclosures."""
        scraper = PodcastUnifiedScraper()

        # Mock entry with audio enclosure
        entry = feedparser.FeedParserDict(
            {
                "enclosures": [
                    feedparser.FeedParserDict(
                        {"href": "https://example.com/episode.mp3", "type": "audio/mpeg"}
                    )
                ]
            }
        )

        audio_url = scraper._find_audio_enclosure(entry, "Test Episode")
        assert audio_url == "https://example.com/episode.mp3"

    def test_find_audio_enclosure_from_links(self):
        """Test extracting audio URL from links when no enclosures."""
        scraper = PodcastUnifiedScraper()

        # Mock entry with no enclosures but audio in links
        entry = feedparser.FeedParserDict(
            {
                "enclosures": [],
                "links": [
                    {"href": "https://example.com/page", "type": "text/html"},
                    {"href": "https://example.com/episode.mp3", "type": "audio/mpeg"},
                ],
            }
        )

        audio_url = scraper._find_audio_enclosure(entry, "Test Episode")
        assert audio_url == "https://example.com/episode.mp3"

    def test_find_audio_enclosure_by_extension(self):
        """Test extracting audio URL by file extension."""
        scraper = PodcastUnifiedScraper()

        # Mock entry with audio URL identified by extension
        entry = feedparser.FeedParserDict(
            {
                "enclosures": [],
                "links": [
                    {"href": "https://example.com/page", "type": ""},
                    {"href": "https://example.com/episode.m4a", "type": ""},
                ],
            }
        )

        audio_url = scraper._find_audio_enclosure(entry, "Test Episode")
        assert audio_url == "https://example.com/episode.m4a"

    def test_find_audio_enclosure_none_found(self):
        """Test when no audio URL can be found."""
        scraper = PodcastUnifiedScraper()

        # Mock entry with no audio
        entry = feedparser.FeedParserDict(
            {"enclosures": [], "links": [{"href": "https://example.com/page", "type": "text/html"}]}
        )

        audio_url = scraper._find_audio_enclosure(entry, "Test Episode")
        assert audio_url is None

    def test_process_entry_with_audio(self):
        """Test processing a podcast entry with audio URL."""
        scraper = PodcastUnifiedScraper()

        # Create a proper mock entry that behaves like feedparser entry
        entry_mock = feedparser.FeedParserDict(
            {
                "title": "Test Podcast Episode",
                "link": "https://example.com/episode",
                "description": "Test description",
                "author": "Test Author",
                "published_parsed": (2025, 6, 14, 12, 0, 0, 0, 0, 0),
                "enclosures": [
                    feedparser.FeedParserDict(
                        {"href": "https://example.com/episode.mp3", "type": "audio/mpeg"}
                    )
                ],
            }
        )

        feed_info = {"title": "Test Podcast"}

        result = scraper._process_entry(
            entry_mock,
            "Test Feed",
            feed_info,
            "https://example.com/rss",
            None,
        )

        assert result is not None
        assert result["title"] == "Test Podcast Episode"
        assert result["url"] == "https://example.com/episode"
        assert result["content_type"] == ContentType.PODCAST
        assert result["metadata"]["audio_url"] == "https://example.com/episode.mp3"

    def test_process_entry_without_audio(self):
        """Test processing a podcast entry without audio URL returns None."""
        scraper = PodcastUnifiedScraper()

        # Mock entry without audio
        entry_mock = feedparser.FeedParserDict(
            {
                "title": "Test Podcast Episode",
                "link": "https://example.com/episode",
                "enclosures": [],
                "links": [],
            }
        )

        feed_info = {"title": "Test Podcast"}

        result = scraper._process_entry(
            entry_mock,
            "Test Feed",
            feed_info,
            "https://example.com/rss",
            None,
        )

        assert result is None  # Should return None when no audio found

    def test_scrape_real_feed(self):
        """Test scraping a podcast feed with mocked parser output."""
        scraper = PodcastUnifiedScraper()
        mock_feed = Mock()
        mock_feed.bozo = 0
        mock_feed.entries = [
            feedparser.FeedParserDict(
                {
                    "title": "Episode One",
                    "link": "https://example.com/ep1",
                    "published_parsed": (2025, 6, 14, 12, 0, 0, 0, 0, 0),
                    "enclosures": [
                        feedparser.FeedParserDict(
                            {"href": "https://example.com/ep1.mp3", "type": "audio/mpeg"}
                        )
                    ],
                }
            ),
            feedparser.FeedParserDict(
                {
                    "title": "Episode Two",
                    "link": "https://example.com/ep2",
                    "published_parsed": (2025, 6, 15, 12, 0, 0, 0, 0, 0),
                    "enclosures": [
                        feedparser.FeedParserDict(
                            {"href": "https://example.com/ep2.mp3", "type": "audio/mpeg"}
                        )
                    ],
                }
            ),
        ]
        mock_feed.feed = {"title": "Lenny's Podcast"}

        with (
            patch("app.scraping.podcast_unified.fetch_and_parse_feed", return_value=mock_feed),
            patch.object(
                PodcastUnifiedScraper,
                "_load_podcast_feeds",
                return_value=[
                    {
                        "name": "Lenny's Podcast",
                        "url": "https://example.com/rss",
                        "limit": 2,
                    }
                ],
            ),
        ):
            items = scraper.scrape()

        assert len(items) == 2
        for item in items:
            assert item["title"]
            assert item["url"]
            assert item["content_type"] == ContentType.PODCAST
            assert item["metadata"]["audio_url"]
            assert item["metadata"]["feed_name"] == "Lenny's Podcast"


class TestPodcastMediaWorkerHelpers:
    """Test podcast media worker helper functionality."""

    def test_extract_anchor_redirect_url(self):
        """Test extracting actual audio URL from Anchor.fm redirect."""
        worker = PodcastMediaWorker()

        redirect_url = "https://anchor.fm/s/f06c2370/podcast/play/103731634/https%3A%2F%2Fd3ctxlq1ktw2nl.cloudfront.net%2Fstaging%2F2025-5-14%2F394074513-44100-2-77e5a2cb613f6.mp3"

        actual_url = worker._extract_actual_audio_url(redirect_url)

        assert (
            actual_url
            == "https://d3ctxlq1ktw2nl.cloudfront.net/staging/2025-5-14/394074513-44100-2-77e5a2cb613f6.mp3"
        )

    def test_extract_normal_url(self):
        """Test that normal URLs are returned unchanged."""
        worker = PodcastMediaWorker()

        normal_url = "https://example.com/episode.mp3"

        actual_url = worker._extract_actual_audio_url(normal_url)

        assert actual_url == normal_url

    def test_validate_url_valid(self):
        """Test URL validation with valid URL."""
        worker = PodcastMediaWorker()

        assert worker._validate_url("https://example.com/episode.mp3") is True
        assert worker._validate_url("http://example.com/episode.mp3") is True

    def test_validate_url_invalid(self):
        """Test URL validation with invalid URLs."""
        worker = PodcastMediaWorker()

        assert worker._validate_url("not a url") is False
        assert worker._validate_url("") is False
        assert worker._validate_url("https://example.com/episode with spaces.mp3") is False
        assert worker._validate_url("example.com/episode.mp3") is False  # No scheme

    def test_process_download_no_audio_url(self):
        """Test download fails gracefully when no audio URL in metadata."""
        worker = PodcastMediaWorker()

        # Mock database and content, and the domain converter
        with (
            patch("app.pipeline.podcast_workers.get_db") as mock_get_db,
            patch("app.pipeline.podcast_workers.content_to_domain") as mock_content_to_domain,
        ):
            mock_db = Mock()
            mock_get_db.return_value.__enter__.return_value = mock_db

            # Content without audio_url in metadata
            mock_content = Mock(
                id=1,
                title="Test Podcast",
                url="https://example.com/episode",
                content_type=ContentType.PODCAST.value,
                content_metadata={"feed_name": "Test Feed"},
                status=ContentStatus.NEW.value,
                error_message=None,
            )

            mock_domain_content = ContentData(
                id=1,
                content_type=ContentType.PODCAST,
                url="https://example.com/episode",
                status=ContentStatus.NEW,
                metadata={"feed_name": "Test Feed"},
            )
            mock_content_to_domain.return_value = mock_domain_content

            mock_db.query.return_value.filter.return_value.first.return_value = mock_content

            result = worker.process_media_task(1)

            assert result is False
            assert mock_content.status == ContentStatus.FAILED.value
            assert mock_content.error_message == "No audio URL found"
            mock_db.commit.assert_called()

    def test_process_download_invalid_url(self):
        """Test download fails gracefully with invalid audio URL."""
        worker = PodcastMediaWorker()

        with (
            patch("app.pipeline.podcast_workers.get_db") as mock_get_db,
            patch("app.pipeline.podcast_workers.content_to_domain") as mock_content_to_domain,
        ):
            mock_db = Mock()
            mock_get_db.return_value.__enter__.return_value = mock_db

            # Content with invalid audio_url
            mock_content = Mock(
                id=1,
                title="Test Podcast",
                url="https://example.com/episode",
                content_type=ContentType.PODCAST.value,
                content_metadata={"feed_name": "Test Feed", "audio_url": "not a valid url"},
                status=ContentStatus.NEW.value,
                error_message=None,
                retry_count=0,
            )

            mock_domain_content = ContentData(
                id=1,
                content_type=ContentType.PODCAST,
                url="https://example.com/episode",
                status=ContentStatus.NEW,
                metadata={"feed_name": "Test Feed", "audio_url": "not a valid url"},
            )
            mock_content_to_domain.return_value = mock_domain_content

            mock_db.query.return_value.filter.return_value.first.return_value = mock_content

            result = worker.process_media_task(1)

            assert result is False
            assert mock_content.status == ContentStatus.FAILED.value
            assert "Invalid audio URL format" in mock_content.error_message
            mock_db.commit.assert_called()


class TestPodcastIntegration:
    """Integration tests for the complete podcast pipeline."""

    def test_scrape_and_store_podcast(self):
        """Test that scraped podcasts have audio URLs in metadata."""
        scraper = PodcastUnifiedScraper()
        mock_feed = Mock()
        mock_feed.bozo = 0
        mock_feed.entries = [
            {
                "title": "Test Episode",
                "link": "https://example.com/episode",
                "published_parsed": (2025, 6, 14, 12, 0, 0, 0, 0, 0),
                "enclosures": [Mock(href="https://example.com/audio/test.mp3", type="audio/mpeg")],
            }
        ]
        mock_feed.feed = {"title": "Test Feed"}

        with (
            patch("app.scraping.podcast_unified.fetch_and_parse_feed", return_value=mock_feed),
            patch.object(
                PodcastUnifiedScraper,
                "_load_podcast_feeds",
                return_value=[{"name": "Test Feed", "url": "https://example.com/rss", "limit": 1}],
            ),
        ):
            items = scraper.scrape()

        # Verify we got items with audio URLs
        assert len(items) > 0

        for item in items:
            # Check required fields
            assert item["title"]
            assert item["url"]
            assert item["content_type"] == ContentType.PODCAST

            # Most importantly, check audio URL exists
            assert "metadata" in item
            assert "audio_url" in item["metadata"]
            assert item["metadata"]["audio_url"].startswith("http")

            # Verify it's a valid audio URL
            audio_url = item["metadata"]["audio_url"]
            assert any(ext in audio_url.lower() for ext in [".mp3", ".m4a", "audio"])
