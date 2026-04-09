"""Integration tests using real data fixtures.

These tests demonstrate using the content_samples fixtures with the processing
pipeline, showing how real data flows through the system.
"""

from unittest.mock import Mock, patch

import pytest

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import ProcessingTask
from app.pipeline.worker import ContentWorker
from app.services.queue import QueueService, TaskType


class TestPipelineWithRealData:
    """Test processing pipeline with real data examples."""

    @pytest.mark.integration
    def test_process_article_with_real_structure(
        self, db_session, sample_unprocessed_article, create_sample_content
    ):
        """Test processing an article using real content structure from fixtures."""
        content = create_sample_content(sample_unprocessed_article, visible=False)
        assert content.status == ContentStatus.NEW.value

        # Mock external dependencies
        with (
            patch("app.pipeline.worker.get_http_service"),
            patch("app.pipeline.worker.get_strategy_registry") as mock_registry,
            patch("app.pipeline.worker.get_checkout_manager"),
            patch("app.pipeline.worker.get_task_queue_gateway") as mock_queue_gateway,
        ):
            # Setup strategy mock
            mock_strategy = Mock()
            mock_strategy.preprocess_url.return_value = content.url
            mock_strategy.download_content.return_value = (
                sample_unprocessed_article["content_metadata"]["content"]
            )
            mock_strategy.extract_data.return_value = {
                "title": sample_unprocessed_article["title"],
                "text_content": sample_unprocessed_article["content_metadata"]["content"],
                "content_type": "html",
                "final_url_after_redirects": content.url,
            }
            mock_strategy.prepare_for_llm.return_value = {
                "content_to_summarize": sample_unprocessed_article["content_metadata"]["content"]
            }
            mock_strategy.extract_internal_urls.return_value = []

            mock_registry_instance = Mock()
            mock_registry_instance.get_strategy.return_value = mock_strategy
            mock_registry.return_value = mock_registry_instance

            # Process the content
            worker = ContentWorker()
            result = worker.process_content(content.id, "test-worker")

            assert result is True

            # Verify content was updated
            db_session.refresh(content)
            assert content.status == ContentStatus.PROCESSING.value
            assert content.content_metadata.get("content") is None
            assert content.content_metadata.get("excerpt")
            mock_queue_gateway.return_value.enqueue.assert_called_with(
                TaskType.SUMMARIZE, content_id=content.id
            )

    @pytest.mark.integration
    def test_process_podcast_with_real_structure(
        self, db_session, sample_unprocessed_podcast, create_sample_content
    ):
        """Test processing a podcast using real transcript structure."""
        content = create_sample_content(sample_unprocessed_podcast, visible=False)
        assert content.status == sample_unprocessed_podcast["status"]
        assert "transcript" in content.content_metadata

        # Mock dependencies
        with (
            patch("app.pipeline.worker.get_checkout_manager"),
            patch("app.pipeline.worker.get_task_queue_gateway") as mock_queue_gateway,
            patch("app.pipeline.worker.PodcastMediaWorker"),
        ):
            # Process the content
            worker = ContentWorker()
            worker.process_content(content.id, "test-worker")

            # For podcasts with transcript, we should summarize it
            db_session.refresh(content)

            mock_queue_gateway.return_value.enqueue.assert_any_call(
                TaskType.PROCESS_PODCAST_MEDIA, content_id=content.id
            )
            mock_queue_gateway.return_value.enqueue.assert_any_call(
                TaskType.SUMMARIZE, content_id=content.id
            )

    @pytest.mark.integration
    def test_completed_article_structure_matches_fixture(
        self, db_session, sample_article_long, create_sample_content
    ):
        """Verify that completed articles in DB match the structure of our fixtures."""
        content = create_sample_content(sample_article_long, visible=False)

        # Verify it has all expected fields
        assert content.status == ContentStatus.COMPLETED.value
        assert content.content_metadata is not None

        # Verify summary structure
        summary = content.content_metadata.get("summary")
        assert summary is not None
        assert "title" in summary
        assert "overview" in summary
        assert "bullet_points" in summary
        assert isinstance(summary["bullet_points"], list)

        # Verify bullet points structure
        for bp in summary["bullet_points"]:
            assert "text" in bp
            assert "category" in bp

        # Verify quotes structure
        if "quotes" in summary:
            for quote in summary["quotes"]:
                assert "text" in quote
                assert "context" in quote

        # Verify topics
        assert "topics" in summary
        assert isinstance(summary["topics"], list)

    @pytest.mark.integration
    def test_processing_different_content_types(
        self,
        db_session,
        sample_article_short,
        sample_podcast,
        create_sample_content,
    ):
        """Test that different content types can be processed with their fixtures."""
        article = create_sample_content(sample_article_short, visible=False)
        podcast = create_sample_content(sample_podcast, visible=False)

        # Verify they're in the database with correct types
        assert article.content_type == ContentType.ARTICLE.value
        assert podcast.content_type == ContentType.PODCAST.value

        # Verify article has HTML content
        assert article.content_metadata.get("content_type") == "html"
        assert "content" in article.content_metadata

        # Verify podcast has audio metadata
        assert "transcript" in podcast.content_metadata
        assert "audio_url" in podcast.content_metadata
        assert "duration_seconds" in podcast.content_metadata

    @pytest.mark.integration
    def test_queue_and_process_with_fixtures(
        self, db_session, sample_unprocessed_article, create_sample_content
    ):
        """Test complete flow: create content, queue task, process."""
        content = create_sample_content(sample_unprocessed_article, visible=False)

        # Queue processing task
        queue_service = QueueService()
        task_id = queue_service.enqueue(
            task_type=TaskType.PROCESS_CONTENT,
            content_id=content.id,
        )

        assert task_id is not None

        # Dequeue the task
        task = queue_service.dequeue(worker_id="test-worker")
        assert task is not None
        assert task["content_id"] == content.id

        # Mark task complete
        queue_service.complete_task(task["id"], success=True)

        # Verify task is completed
        completed_task = db_session.query(ProcessingTask).filter_by(id=task["id"]).first()
        assert completed_task.status == "completed"

    @pytest.mark.integration
    def test_article_metadata_preservation(
        self, db_session, sample_article_long, create_sample_content
    ):
        """Ensure all metadata fields from fixtures are preserved in DB."""
        content = create_sample_content(sample_article_long, visible=False)

        # Verify metadata was preserved
        metadata = content.content_metadata
        assert metadata.get("source") == sample_article_long["content_metadata"]["source"]
        assert (
            metadata.get("content_type")
            == sample_article_long["content_metadata"]["content_type"]
        )

        # Verify HackerNews metadata
        if "hn_id" in sample_article_long["content_metadata"]:
            assert metadata.get("hn_id") == sample_article_long["content_metadata"]["hn_id"]
            assert metadata.get("score") == sample_article_long["content_metadata"]["score"]

        # Verify summary was preserved
        assert "summary" in metadata
        fixture_summary = sample_article_long["content_metadata"]["summary"]
        db_summary = metadata["summary"]

        assert db_summary["title"] == fixture_summary["title"]
        assert db_summary["overview"] == fixture_summary["overview"]
        assert len(db_summary["bullet_points"]) == len(fixture_summary["bullet_points"])
