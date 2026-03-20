"""Tests for ContentWorker."""

from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from app.models.metadata import (
    ContentData,
    ContentStatus,
    ContentType,
)
from app.pipeline.worker import ContentWorker
from app.processing_strategies.youtube_strategy import YouTubeProcessorStrategy
from app.services.http import NonRetryableError
from app.services.queue import TaskType


@pytest.fixture
def mock_dependencies():
    """Mock all external dependencies."""
    with (
        patch("app.pipeline.worker.get_checkout_manager") as mock_checkout,
        patch("app.pipeline.worker.get_http_service") as mock_http,
        patch("app.pipeline.worker.get_queue_service") as mock_queue,
        patch("app.pipeline.worker.get_task_queue_gateway") as mock_queue_gateway,
        patch("app.pipeline.worker.get_strategy_registry") as mock_registry,
        patch("app.pipeline.worker.PodcastDownloadWorker") as mock_download,
        patch("app.pipeline.worker.PodcastTranscribeWorker") as mock_transcribe,
        patch("app.pipeline.worker.get_db") as mock_get_db,
    ):
        yield {
            "checkout": mock_checkout,
            "http": mock_http,
            "queue": mock_queue,
            "queue_gateway": mock_queue_gateway,
            "registry": mock_registry,
            "download": mock_download,
            "transcribe": mock_transcribe,
            "get_db": mock_get_db,
        }


class TestContentWorker:
    """Test cases for ContentWorker."""

    def test_init(self, mock_dependencies):
        """Test worker initialization."""
        worker = ContentWorker()

        assert worker.checkout_manager is not None
        assert worker.http_service is not None
        assert worker.queue_service is not None
        assert worker.strategy_registry is not None
        assert worker.podcast_download_worker is not None
        assert worker.podcast_transcribe_worker is not None

    def test_process_content_not_found(self, mock_dependencies):
        """Test processing when content not found."""
        worker = ContentWorker()

        # Mock database to return no content
        mock_db = Mock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_dependencies["get_db"].return_value.__enter__.return_value = mock_db

        result = worker.process_content(123, "test-worker")

        assert result is False

    def test_process_article_sync_success(self, mock_dependencies):
        """Test successful article processing."""
        worker = ContentWorker()

        # Create mock content
        mock_content = Mock()
        mock_content.id = 123
        mock_content.url = "https://example.com/article"
        mock_content.content_type = ContentType.ARTICLE.value
        mock_content.content_metadata = {}

        # Convert to domain model
        content_data = ContentData(
            id=123,
            url="https://example.com/article",
            content_type=ContentType.ARTICLE,
            status=ContentStatus.NEW,
            metadata={},
            title="Test Article",
            created_at=datetime.now(UTC),
            processed_at=None,
            retry_count=0,
        )

        # Mock database
        mock_db = Mock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_content
        mock_dependencies["get_db"].return_value.__enter__.return_value = mock_db

        # Mock strategy
        mock_strategy = Mock()
        mock_strategy.preprocess_url.return_value = "https://example.com/article"
        mock_strategy.download_content.return_value = "<html>content</html>"
        mock_strategy.extract_data.return_value = {
            "title": "Test Article",
            "text_content": "This is test content.",
            "author": "Test Author",
            "publication_date": None,
            "content_type": "html",
            "final_url_after_redirects": "https://example.com/article",
        }
        mock_strategy.prepare_for_llm.return_value = {
            "content_to_summarize": "This is test content."
        }
        mock_strategy.extract_internal_urls.return_value = []

        worker.strategy_registry.get_strategy.return_value = mock_strategy

        # Mock content_to_domain function
        with patch("app.pipeline.worker.content_to_domain") as mock_converter:
            mock_converter.return_value = content_data

            result = worker.process_content(123, "test-worker")

        # Verify success - article extraction now enqueues SUMMARIZE task
        assert result is True
        mock_strategy.download_content.assert_called_once_with("https://example.com/article")
        mock_strategy.extract_data.assert_called_once()
        # Verify SUMMARIZE task was enqueued (summarization happens asynchronously)
        worker.queue_gateway.enqueue.assert_called_with(TaskType.SUMMARIZE, content_id=123)
        mock_db.commit.assert_called()
        # Content is stored in metadata for the SUMMARIZE task
        assert content_data.metadata.get("content") is not None

    def test_process_article_sync_no_strategy(self, mock_dependencies):
        """Test article processing when no strategy available."""
        worker = ContentWorker()

        # Create mock content
        mock_content = Mock()
        mock_content.id = 123
        mock_content.url = "https://example.com/article"
        mock_content.content_type = ContentType.ARTICLE.value

        content_data = ContentData(
            id=123,
            url="https://example.com/article",
            content_type=ContentType.ARTICLE,
            status=ContentStatus.NEW,
            metadata={},
            title="Test Article",
            created_at=datetime.now(UTC),
            processed_at=None,
            retry_count=0,
        )

        # Mock database
        mock_db = Mock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_content
        mock_dependencies["get_db"].return_value.__enter__.return_value = mock_db

        # No strategy available
        worker.strategy_registry.get_strategy.return_value = None

        with patch("app.pipeline.worker.content_to_domain") as mock_converter:
            mock_converter.return_value = content_data

            result = worker.process_content(123, "test-worker")

        assert result is False

    def test_process_article_sync_non_retryable_error(self, mock_dependencies):
        """Test article processing with non-retryable error."""
        worker = ContentWorker()

        # Create mock content
        mock_content = Mock()
        mock_content.id = 123
        mock_content.url = "https://example.com/article"
        mock_content.content_type = ContentType.ARTICLE.value
        mock_content.content_metadata = {}

        content_data = ContentData(
            id=123,
            url="https://example.com/article",
            content_type=ContentType.ARTICLE,
            status=ContentStatus.NEW,
            metadata={},
            title="Test Article",
            created_at=datetime.now(UTC),
            processed_at=None,
            retry_count=0,
        )

        # Mock database
        mock_db = Mock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_content
        mock_dependencies["get_db"].return_value.__enter__.return_value = mock_db

        # Mock strategy to raise a non-retryable error during download
        mock_strategy = Mock()
        mock_strategy.preprocess_url.return_value = "https://example.com/article"
        mock_strategy.download_content.side_effect = NonRetryableError(
            "Non-retryable HTTP 403: Forbidden"
        )
        worker.strategy_registry.get_strategy.return_value = mock_strategy

        with patch("app.pipeline.worker.content_to_domain") as mock_converter:
            mock_converter.return_value = content_data

            result = worker.process_content(123, "test-worker")

        assert result is False

    def test_resolve_article_url_for_news(self):
        """News items with primary article metadata should resolve to that URL."""
        worker = ContentWorker()

        news_content = ContentData(
            id=77,
            url="https://example.com/story",
            content_type=ContentType.NEWS,
            status=ContentStatus.NEW,
            metadata={
                "platform": "techmeme",
                "article": {"url": "http://example.com/story"},
                "discussion_url": "https://www.techmeme.com/cluster",
            },
            created_at=datetime.now(UTC),
        )

        resolved = worker._resolve_article_url(news_content)
        assert resolved == "https://example.com/story"

    def test_process_news_story(self, mock_dependencies):
        """Ensure news content extraction enqueues SUMMARIZE task."""
        worker = ContentWorker()

        mock_content = Mock()
        mock_content.id = 501
        mock_content.url = "https://example.com/story"
        mock_content.content_type = ContentType.NEWS.value

        metadata = {
            "platform": "techmeme",
            "source": "example.com",
            "article": {
                "url": "https://example.com/story",
                "title": "Original headline",
            },
            "aggregator": {
                "name": "Techmeme",
                "metadata": {"related_links": []},
            },
            "discussion_url": "https://www.techmeme.com/cluster",
        }

        content_data = ContentData(
            id=501,
            url="https://example.com/story",
            content_type=ContentType.NEWS,
            status=ContentStatus.NEW,
            metadata=metadata,
            title="Original headline",
            created_at=datetime.now(UTC),
        )

        mock_db = Mock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_content
        mock_dependencies["get_db"].return_value.__enter__.return_value = mock_db

        mock_strategy = Mock()
        mock_strategy.preprocess_url.return_value = "https://example.com/story"
        mock_strategy.extract_data.return_value = {
            "title": "Example Story",
            "text_content": "Body of the article",
            "content_type": "html",
            "source": "example.com",
            "final_url_after_redirects": "https://example.com/story",
        }
        mock_strategy.prepare_for_llm.return_value = {
            "content_to_summarize": "Body of the article",
        }
        mock_strategy.extract_internal_urls.return_value = []
        worker.strategy_registry.get_strategy.return_value = mock_strategy

        with patch("app.pipeline.worker.content_to_domain") as mock_converter:
            mock_converter.return_value = content_data
            result = worker.process_content(501, "test-worker")

        # News extraction succeeds and enqueues SUMMARIZE task
        assert result is True
        # Content is stored in metadata for SUMMARIZE task
        assert content_data.metadata.get("content") is not None
        # SUMMARIZE task is enqueued
        worker.queue_gateway.enqueue.assert_called_with(TaskType.SUMMARIZE, content_id=501)

    def test_process_article_sync_extraction_error(self, mock_dependencies):
        """Test article processing with extraction error."""
        worker = ContentWorker()

        # Create mock content
        mock_content = Mock()
        mock_content.id = 123
        mock_content.url = "https://example.com/article"
        mock_content.content_type = ContentType.ARTICLE.value

        content_data = ContentData(
            id=123,
            url="https://example.com/article",
            content_type=ContentType.ARTICLE,
            status=ContentStatus.NEW,
            metadata={},
            title="Test Article",
            created_at=datetime.now(UTC),
            processed_at=None,
            retry_count=0,
        )

        # Mock database
        mock_db = Mock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_content
        mock_dependencies["get_db"].return_value.__enter__.return_value = mock_db

        # Mock strategy
        mock_strategy = Mock()
        mock_strategy.preprocess_url.return_value = "https://example.com/article"
        mock_strategy.download_content.return_value = "<html>content</html>"
        mock_strategy.extract_data.side_effect = Exception("Extraction failed")
        worker.strategy_registry.get_strategy.return_value = mock_strategy

        with patch("app.pipeline.worker.content_to_domain") as mock_converter:
            mock_converter.return_value = content_data

            result = worker.process_content(123, "test-worker")

        assert result is False

    def test_process_article_marks_failed_on_crawl_fallback(self, mock_dependencies):
        """Ensure crawl fallback metadata marks the item as failed instead of completed."""
        worker = ContentWorker()

        mock_content = Mock()
        mock_content.id = 1472
        mock_content.url = "https://signalsandthreads.com/why-ml-needs-a-new-programming-language"
        mock_content.content_type = ContentType.ARTICLE.value

        content_data = ContentData(
            id=1472,
            url="https://signalsandthreads.com/why-ml-needs-a-new-programming-language",
            content_type=ContentType.ARTICLE,
            status=ContentStatus.NEW,
            metadata={},
            title="Why ML Needs a New Programming Language",
            created_at=datetime.now(UTC),
        )

        mock_db = Mock()
        filter_result = mock_db.query.return_value.filter.return_value
        filter_result.first.side_effect = [mock_content, mock_content]
        mock_dependencies["get_db"].return_value.__enter__.return_value = mock_db

        fallback_error = "Crawl4ai extraction failed (Unknown error)"

        mock_strategy = Mock()
        mock_strategy.preprocess_url.return_value = (
            "https://signalsandthreads.com/why-ml-needs-a-new-programming-language"
        )
        mock_strategy.download_content.return_value = "<html>content</html>"
        mock_strategy.extract_data.return_value = {
            "title": "Content from https://signalsandthreads.com/why-ml-needs-a-new-programming-language",
            "text_content": (
                "Failed to extract content from https://signalsandthreads.com/why-ml-needs-a-new-"
                "programming-language. Error: Crawl4ai extraction failed (Unknown error)"
            ),
            "content_type": "html",
            "source": "signalsandthreads.com",
            "final_url_after_redirects": (
                "https://signalsandthreads.com/why-ml-needs-a-new-programming-language"
            ),
            "extraction_error": fallback_error,
        }
        mock_strategy.prepare_for_llm.return_value = {
            "content_to_summarize": (
                "Failed to extract content from https://signalsandthreads.com/why-ml-needs-a-new-"
                "programming-language. Error: Crawl4ai extraction failed (Unknown error)"
            )
        }
        mock_strategy.extract_internal_urls.return_value = []
        worker.strategy_registry.get_strategy.return_value = mock_strategy

        with (
            patch("app.pipeline.worker.content_to_domain") as mock_converter,
            patch("app.pipeline.worker.domain_to_content") as mock_domain_to_content,
        ):
            mock_converter.return_value = content_data

            result = worker.process_content(1472, "test-worker")

        # Extraction failure marks content as failed, no SUMMARIZE task is enqueued
        assert result is True
        assert content_data.status == ContentStatus.FAILED
        assert content_data.error_message == fallback_error
        assert content_data.metadata.get("extraction_failed") is True
        assert "summary" not in content_data.metadata
        assert content_data.metadata.get("content") is None
        # No SUMMARIZE task should be enqueued for extraction failures
        worker.queue_gateway.enqueue.assert_not_called()
        mock_domain_to_content.assert_called_once_with(content_data, mock_content)
        # Commit is called to persist the failed status
        mock_db.commit.assert_called()

    def test_process_podcast_sync_success(self, mock_dependencies):
        """Test successful podcast processing."""
        worker = ContentWorker()

        # Create mock content
        mock_content = Mock()
        mock_content.id = 456
        mock_content.url = "https://example.com/podcast.mp3"
        mock_content.content_type = ContentType.PODCAST.value
        mock_content.content_metadata = {"audio_url": "https://example.com/podcast.mp3"}

        content_data = ContentData(
            id=456,
            url="https://example.com/podcast.mp3",
            content_type=ContentType.PODCAST,
            status=ContentStatus.NEW,
            metadata={"audio_url": "https://example.com/podcast.mp3"},
            title="Test Podcast",
            created_at=datetime.now(UTC),
            processed_at=None,
            retry_count=0,
        )

        # Mock database
        mock_db = Mock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_content
        mock_dependencies["get_db"].return_value.__enter__.return_value = mock_db

        # Mock podcast workers
        worker.podcast_download_worker.process_download_task.return_value = True
        worker.podcast_transcribe_worker.process_transcribe_task.return_value = True

        # Mock queue gateway for task creation
        worker.queue_gateway.enqueue.side_effect = [1, 2]  # Return task IDs

        with patch("app.pipeline.worker.content_to_domain") as mock_converter:
            mock_converter.return_value = content_data

            result = worker._process_podcast_sync(content_data)

        assert result is True
        # Should have created download task (since file_path is not in metadata)
        assert worker.queue_gateway.enqueue.call_count == 1
        worker.queue_gateway.enqueue.assert_called_with(TaskType.DOWNLOAD_AUDIO, content_id=456)

    def test_process_youtube_podcast_enqueues_summarize_without_download(
        self,
        mock_dependencies,
    ):
        """YouTube podcast links should summarize from extracted metadata before download."""
        worker = ContentWorker()

        mock_content = Mock()
        mock_content.id = 789
        mock_content.url = "https://www.youtube.com/watch?v=abc123xyz"
        mock_content.content_type = ContentType.PODCAST.value
        mock_content.content_metadata = {
            "audio_url": "https://www.youtube.com/watch?v=abc123xyz",
            "youtube_video": True,
            "platform": "youtube",
        }

        content_data = ContentData(
            id=789,
            url="https://www.youtube.com/watch?v=abc123xyz",
            content_type=ContentType.PODCAST,
            status=ContentStatus.NEW,
            metadata={
                "audio_url": "https://www.youtube.com/watch?v=abc123xyz",
                "youtube_video": True,
                "platform": "youtube",
            },
            title=None,
            created_at=datetime.now(UTC),
            processed_at=None,
            retry_count=0,
        )

        mock_db = Mock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_content
        mock_dependencies["get_db"].return_value.__enter__.return_value = mock_db

        mock_strategy = Mock(spec=YouTubeProcessorStrategy)
        mock_strategy.preprocess_url.return_value = "https://www.youtube.com/watch?v=abc123xyz"
        mock_strategy.download_content.return_value = b""
        mock_strategy.extract_data.return_value = {
            "title": "Sample YouTube Video",
            "author": "Channel Name",
            "publication_date": "2026-03-10T00:00:00+00:00",
            "final_url_after_redirects": "https://www.youtube.com/watch?v=abc123xyz",
            "video_id": "abc123xyz",
            "thumbnail_url": "https://img.youtube.com/vi/abc123xyz/maxresdefault.jpg",
            "metadata": {
                "platform": "youtube",
                "video_id": "abc123xyz",
                "channel": "Channel Name",
                "transcript": "Transcript text",
                "youtube_video": True,
            },
        }
        mock_strategy.prepare_for_llm.return_value = {
            "content_to_filter": "Prepared summary input",
            "content_to_summarize": "Prepared summary input",
        }
        worker.strategy_registry.get_strategy.return_value = mock_strategy

        with patch("app.pipeline.worker.content_to_domain") as mock_converter:
            mock_converter.return_value = content_data

            result = worker.process_content(789, "test-worker")

        assert result is True
        worker.queue_gateway.enqueue.assert_called_once_with(TaskType.SUMMARIZE, content_id=789)
        assert content_data.metadata["content_to_summarize"] == "Prepared summary input"
        assert content_data.metadata["transcript"] == "Transcript text"
        assert content_data.metadata["youtube_video"] is True
        assert content_data.status == ContentStatus.PROCESSING

    def test_process_unknown_content_type(self, mock_dependencies):
        """Test processing with unknown content type."""
        worker = ContentWorker()

        # Create mock content with invalid type
        mock_content = Mock()
        mock_content.id = 789
        mock_content.content_type = "UNKNOWN"

        # Mock database
        mock_db = Mock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_content
        mock_dependencies["get_db"].return_value.__enter__.return_value = mock_db

        with patch("app.pipeline.worker.content_to_domain") as mock_converter:
            # Make content_to_domain raise an error for unknown type
            mock_converter.side_effect = ValueError("Unknown content type")

            result = worker.process_content(789, "test-worker")

        assert result is False

    def test_process_content_article_sync(self, mock_dependencies):
        """Test content processing path for article content."""
        worker = ContentWorker()

        # Create mock content
        mock_content = Mock()
        mock_content.id = 123
        mock_content.url = "https://example.com/article"
        mock_content.content_type = ContentType.ARTICLE.value
        mock_content.content_metadata = {}

        content_data = ContentData(
            id=123,
            url="https://example.com/article",
            content_type=ContentType.ARTICLE,
            status=ContentStatus.NEW,
            metadata={},
            title="Test Article",
            created_at=datetime.now(UTC),
            processed_at=None,
            retry_count=0,
        )

        # Mock database
        mock_db = Mock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_content
        mock_dependencies["get_db"].return_value.__enter__.return_value = mock_db

        # Mock article processing to mark completion
        def fake_process_article(content):
            content.status = ContentStatus.COMPLETED
            return True

        worker._process_article = Mock(side_effect=fake_process_article)

        with patch("app.pipeline.worker.content_to_domain") as mock_converter:
            mock_converter.return_value = content_data

            result = worker.process_content(123, "test-worker")

        assert result is True
        worker._process_article.assert_called_once()
        mock_db.commit.assert_called()
