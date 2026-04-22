from datetime import UTC, datetime

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content, ProcessingTask


class TestContentModel:
    """Test the Content model."""

    def test_content_creation_article(self):
        """Test creating a Content object for an article."""
        content = Content(
            content_type=ContentType.ARTICLE.value,
            url="https://example.com/article",
            title="Test Article",
            source="Import AI",
            status=ContentStatus.NEW.value,
        )

        assert content.content_type == ContentType.ARTICLE.value
        assert content.url == "https://example.com/article"
        assert content.title == "Test Article"
        assert content.source == "Import AI"
        assert content.status == ContentStatus.NEW.value
        # Default is None until saved to DB, where it becomes {}
        assert content.content_metadata is None or content.content_metadata == {}
        assert content.retry_count is None or content.retry_count == 0
        assert content.checked_out_by is None
        assert content.checked_out_at is None
        assert content.error_message is None

    def test_content_creation_podcast(self):
        """Test creating a Content object for a podcast."""
        metadata = {
            "audio_url": "https://example.com/audio.mp3",
            "duration_seconds": 3600,
            "episode_number": 1,
        }

        content = Content(
            content_type=ContentType.PODCAST.value,
            url="https://example.com/podcast/episode1",
            title="Test Podcast Episode",
            source="Lenny's Podcast",
            status=ContentStatus.NEW.value,
            content_metadata=metadata,
        )

        assert content.content_type == ContentType.PODCAST.value
        assert content.url == "https://example.com/podcast/episode1"
        assert content.title == "Test Podcast Episode"
        assert content.source == "Lenny's Podcast"
        # Metadata validation may add fields
        assert content.content_metadata is not None
        assert content.content_metadata["audio_url"] == "https://example.com/audio.mp3"
        assert content.content_metadata["duration_seconds"] == 3600
        assert content.content_metadata["episode_number"] == 1
        assert content.content_metadata["audio_url"] == "https://example.com/audio.mp3"
        assert content.content_metadata["duration_seconds"] == 3600

    def test_content_creation_news(self):
        """Test creating a Content object for news content."""
        metadata = {
            "platform": "twitter",
            "source": "example.com",
            "article": {
                "url": "https://example.com/story",
                "title": "Example Story",
                "source_domain": "example.com",
            },
            "aggregator": {
                "name": "Twitter",
                "title": "@news_bot: Example Story",
                "metadata": {"likes": 10},
            },
            "discussion_url": "https://twitter.com/news_bot/status/1",
            "summary": {
                "title": "Twitter: Example Story",
                "article_url": "https://example.com/story",
                "key_points": ["Key takeaway one", "Key takeaway two"],
                "classification": "to_read",
                "generated_at": datetime.now(UTC).isoformat(),
            },
        }

        content = Content(
            content_type=ContentType.NEWS.value,
            url="https://example.com/story",
            source_url="https://example.com/story",
            title="Daily List",
            platform="twitter",
            source="example.com",
            status=ContentStatus.NEW.value,
            is_aggregate=False,
            content_metadata=metadata,
        )

        assert content.content_type == ContentType.NEWS.value
        assert content.is_aggregate is False
        assert content.content_metadata["article"]["url"] == "https://example.com/story"

    def test_news_metadata_backfills_article(self):
        """Legacy news metadata without article should remain unchanged."""

        legacy_metadata = {
            "platform": "reddit",
            "source": "MachineLearning",
            "items": [
                {
                    "title": "OpenAI ships new model",
                    "url": "https://example.ai/posts/openai-model",
                    "summary": "OpenAI ships new model",
                }
            ],
            "primary_url": "https://reddit.com/r/MachineLearning/comments/xyz",
            "summary": {
                "title": "OpenAI ships new model",
                "overview": "Key highlights from the thread",
            },
        }

        content = Content(
            content_type=ContentType.NEWS.value,
            url="https://example.ai/posts/openai-model",
            title="OpenAI ships new model",
            platform="reddit",
            source="MachineLearning",
            status=ContentStatus.NEW.value,
            is_aggregate=False,
            content_metadata=legacy_metadata,
        )

        assert content.content_metadata.get("article") is None
        assert (
            content.content_metadata["primary_url"]
            == "https://reddit.com/r/MachineLearning/comments/xyz"
        )

    def test_content_metadata_json_field(self):
        """Test that metadata is stored as JSON."""
        complex_metadata = {
            "author": "John Doe",
            "tags": ["tech", "AI", "programming"],
            "publication_date": "2025-06-14",
            "word_count": 1500,
            "nested": {"key": "value", "number": 42},
        }

        content = Content(
            content_type=ContentType.ARTICLE.value,
            url="https://example.com/complex",
            content_metadata=complex_metadata,
        )

        # Metadata validation may transform fields
        assert content.content_metadata is not None
        assert content.content_metadata["author"] == "John Doe"
        assert content.content_metadata["tags"] == ["tech", "AI", "programming"]
        assert content.content_metadata["word_count"] == 1500
        assert content.content_metadata["nested"]["key"] == "value"
        assert content.content_metadata["nested"]["number"] == 42
        assert content.content_metadata["nested"]["number"] == 42

    def test_content_status_transitions(self):
        """Test status transitions."""
        content = Content(content_type=ContentType.ARTICLE.value, url="https://example.com/test")

        # Default status is None until saved to DB
        assert content.status is None or content.status == ContentStatus.NEW.value

        # Transition to processing
        content.status = ContentStatus.PROCESSING.value
        assert content.status == ContentStatus.PROCESSING.value

        # Transition to completed
        content.status = ContentStatus.COMPLETED.value
        content.processed_at = datetime.now(UTC)
        assert content.status == ContentStatus.COMPLETED.value
        assert content.processed_at is not None

    def test_content_checkout_mechanism(self):
        """Test the checkout mechanism for workers."""
        content = Content(
            content_type=ContentType.ARTICLE.value,
            url="https://example.com/checkout-test",
            status=ContentStatus.NEW.value,
        )

        # Initially not checked out
        assert content.checked_out_by is None
        assert content.checked_out_at is None

        # Check out to worker
        content.checked_out_by = "worker-123"
        content.checked_out_at = datetime.now(UTC)
        content.status = ContentStatus.PROCESSING.value

        assert content.checked_out_by == "worker-123"
        assert content.checked_out_at is not None
        assert content.status == ContentStatus.PROCESSING.value

    def test_content_source_field(self):
        """Test the source field on Content model."""
        # Test with source
        content_with_source = Content(
            content_type=ContentType.ARTICLE.value,
            url="https://example.com/source-test",
            source="Tech Emails",
        )
        assert content_with_source.source == "Tech Emails"

        # Test without source (should be None)
        content_without_source = Content(
            content_type=ContentType.ARTICLE.value, url="https://example.com/no-source"
        )
        assert content_without_source.source is None

    def test_content_error_handling(self):
        """Test error message storage."""
        content = Content(
            content_type=ContentType.ARTICLE.value,
            url="https://example.com/error-test",
            status=ContentStatus.FAILED.value,
            error_message="Network timeout",
            retry_count=3,
        )

        assert content.status == ContentStatus.FAILED.value
        assert content.error_message == "Network timeout"
        assert content.retry_count == 3

    def test_content_timestamps(self):
        """Test timestamp handling."""
        now = datetime.now(UTC)

        content = Content(
            content_type=ContentType.ARTICLE.value,
            url="https://example.com/timestamp-test",
            created_at=now,
            updated_at=now,
        )

        assert content.created_at == now
        assert content.updated_at == now

        # Test processed_at
        processed_time = datetime.now(UTC)
        content.processed_at = processed_time
        assert content.processed_at == processed_time


class TestContentTypeEnum:
    """Test ContentType enum."""

    def test_content_type_values(self):
        """Test ContentType enum values."""
        assert ContentType.ARTICLE.value == "article"
        assert ContentType.PODCAST.value == "podcast"
        assert ContentType.NEWS.value == "news"

    def test_content_type_count(self):
        """Test that we have all supported content types."""
        content_types = list(ContentType)
        assert len(content_types) == 5

    def test_content_type_iteration(self):
        """Test iterating over ContentType enum."""
        expected_values = {"article", "podcast", "news", "insight_report", "unknown"}
        actual_values = {ct.value for ct in ContentType}
        assert actual_values == expected_values


class TestContentStatusEnum:
    """Test ContentStatus enum."""

    def test_content_status_values(self):
        """Test ContentStatus enum values."""
        assert ContentStatus.NEW.value == "new"
        assert ContentStatus.PENDING.value == "pending"
        assert ContentStatus.PROCESSING.value == "processing"
        assert ContentStatus.AWAITING_IMAGE.value == "awaiting_image"
        assert ContentStatus.COMPLETED.value == "completed"
        assert ContentStatus.FAILED.value == "failed"
        assert ContentStatus.SKIPPED.value == "skipped"

    def test_content_status_count(self):
        """Test that we have all supported statuses."""
        statuses = list(ContentStatus)
        assert len(statuses) == 7

    def test_content_status_iteration(self):
        """Test iterating over ContentStatus enum."""
        expected_values = {
            "new",
            "pending",
            "processing",
            "awaiting_image",
            "completed",
            "failed",
            "skipped",
        }
        actual_values = {status.value for status in ContentStatus}
        assert actual_values == expected_values


class TestProcessingTaskModel:
    """Test the ProcessingTask model."""

    def test_processing_task_creation(self):
        """Test creating a ProcessingTask object."""
        task = ProcessingTask(
            task_type="process_content", content_id=123, payload={"test": "data"}, status="pending"
        )

        assert task.task_type == "process_content"
        assert task.content_id == 123
        assert task.payload == {"test": "data"}
        assert task.status == "pending"
        assert task.retry_count is None or task.retry_count == 0
        assert task.error_message is None

    def test_processing_task_minimal(self):
        """Test creating a minimal ProcessingTask."""
        task = ProcessingTask(task_type="scrape")

        assert task.task_type == "scrape"
        assert task.content_id is None
        assert task.payload is None or task.payload == {}
        assert task.status is None or task.status == "pending"

    def test_processing_task_timestamps(self):
        """Test ProcessingTask timestamp handling."""
        now = datetime.now(UTC)

        task = ProcessingTask(task_type="test", created_at=now)

        assert task.created_at == now
        assert task.started_at is None
        assert task.completed_at is None

        # Set processing timestamps
        started = datetime.now(UTC)
        completed = datetime.now(UTC)

        task.started_at = started
        task.completed_at = completed

        assert task.started_at == started
        assert task.completed_at == completed

    def test_processing_task_error_handling(self):
        """Test ProcessingTask error handling."""
        task = ProcessingTask(
            task_type="test", status="failed", error_message="Test error", retry_count=2
        )

        assert task.status == "failed"
        assert task.error_message == "Test error"
        assert task.retry_count == 2


class TestModelIntegration:
    """Integration tests for model relationships."""

    def test_content_with_multiple_tasks(self):
        """Test content that has multiple processing tasks."""
        # Create content
        content = Content(
            content_type=ContentType.ARTICLE.value, url="https://example.com/multi-task"
        )
        content.id = 123  # Simulate database ID

        # Create related tasks
        task1 = ProcessingTask(
            task_type="process_content", content_id=content.id, status="completed"
        )

        task2 = ProcessingTask(task_type="summarize", content_id=content.id, status="pending")

        # Verify relationship
        assert task1.content_id == content.id
        assert task2.content_id == content.id

    def test_article_workflow_simulation(self):
        """Test simulating an article processing workflow."""
        # 1. Create new article content
        content = Content(
            content_type=ContentType.ARTICLE.value,
            url="https://example.com/workflow-test",
            title="Workflow Test Article",
            source="AI Snake Oil",
            status=ContentStatus.NEW.value,
        )

        # 2. Create processing task
        task = ProcessingTask(
            task_type="process_content",
            content_id=123,  # Simulate content ID
            status="pending",
        )

        # 3. Simulate processing
        content.status = ContentStatus.PROCESSING.value
        content.checked_out_by = "worker-1"
        content.checked_out_at = datetime.now(UTC)

        task.status = "processing"
        task.started_at = datetime.now(UTC)

        # 4. Simulate completion
        content.status = ContentStatus.COMPLETED.value
        content.processed_at = datetime.now(UTC)
        content.content_metadata = {
            "author": "Test Author",
            "word_count": 1200,
            "summary": "Test summary",
        }

        task.status = "completed"
        task.completed_at = datetime.now(UTC)

        # Verify final state
        assert content.status == ContentStatus.COMPLETED.value
        assert content.processed_at is not None
        assert content.content_metadata["word_count"] == 1200
        assert task.status == "completed"
        assert task.completed_at is not None

    def test_podcast_workflow_simulation(self):
        """Test simulating a podcast processing workflow."""
        # 1. Create new podcast content
        content = Content(
            content_type=ContentType.PODCAST.value,
            url="https://example.com/podcast/episode",
            title="Test Podcast Episode",
            source="BG2 Pod",
            content_metadata={"audio_url": "https://example.com/audio.mp3", "episode_number": 1},
        )

        # 2. Create download task
        download_task = ProcessingTask(
            task_type="download_audio", content_id=456, status="completed"
        )

        # 3. Create transcription task
        transcribe_task = ProcessingTask(task_type="transcribe", content_id=456, status="completed")

        # 4. Update content with transcript
        content.content_metadata.update(
            {"transcript": "This is the podcast transcript...", "duration_seconds": 3600}
        )
        content.status = ContentStatus.COMPLETED.value

        # Verify final state
        assert content.content_type == ContentType.PODCAST.value
        assert "transcript" in content.content_metadata
        assert content.content_metadata["duration_seconds"] == 3600
        assert download_task.task_type == "download_audio"
        assert transcribe_task.task_type == "transcribe"
