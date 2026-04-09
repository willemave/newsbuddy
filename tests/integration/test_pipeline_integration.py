"""Integration tests for the complete processing pipeline."""

from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from app.core.db import get_db
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content, ContentBody, ProcessingTask
from app.pipeline.sequential_task_processor import SequentialTaskProcessor
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import QueueService, TaskType


class _SummaryStub:
    """Minimal serializable summary stub for integration tests."""

    def __init__(self, summary: str) -> None:
        self._summary = summary

    def model_dump(self, mode: str = "json", by_alias: bool = True) -> dict[str, str]:
        del mode, by_alias
        return {"summary": self._summary}


@pytest.fixture
def setup_test_db(db_session):
    """Setup an isolated test database with sample content."""
    test_contents = [
        Content(
            url="https://example.com/article1",
            title="Test Article 1",
            content_type=ContentType.ARTICLE.value,
            status=ContentStatus.NEW.value,
            created_at=datetime.now(UTC),
            content_metadata={},
        ),
        Content(
            url="https://example.com/article2",
            title="Test Article 2",
            content_type=ContentType.ARTICLE.value,
            status=ContentStatus.NEW.value,
            created_at=datetime.now(UTC),
            content_metadata={},
        ),
        Content(
            url="https://failing-site.com/article",
            title="Failing Article",
            content_type=ContentType.ARTICLE.value,
            status=ContentStatus.NEW.value,
            created_at=datetime.now(UTC),
            content_metadata={},
        ),
    ]

    for content in test_contents:
        db_session.add(content)
    db_session.commit()
    for content in test_contents:
        db_session.refresh(content)
    yield {
        "article1_id": test_contents[0].id,
        "article2_id": test_contents[1].id,
        "failing_article_id": test_contents[2].id,
    }

class TestPipelineIntegration:
    """Integration tests for the complete pipeline."""

    @pytest.mark.integration
    def test_full_article_processing_pipeline(self, setup_test_db):
        """Test complete article processing from task creation to completion."""
        article_id = setup_test_db["article1_id"]

        # Create processing task
        queue_service = QueueService()
        task_id = queue_service.enqueue(task_type=TaskType.PROCESS_CONTENT, content_id=article_id)

        assert task_id is not None

        # Mock external services
        with (
            patch("app.pipeline.worker.get_http_service") as mock_http_service,
            patch("app.pipeline.sequential_task_processor.get_llm_service") as mock_llm_service,
            patch("app.pipeline.worker.get_strategy_registry") as mock_registry,
        ):
            # Setup mocks
            mock_http = Mock()
            mock_http.fetch_content.return_value = (
                "<html><body>Test article content</body></html>",
                {"content-type": "text/html"},
            )
            mock_http_service.return_value = mock_http

            mock_llm = Mock()
            mock_summary = _SummaryStub("Test summary")
            mock_llm.summarize.return_value = mock_summary
            mock_llm_service.return_value = mock_llm

            mock_strategy = Mock()
            mock_strategy.preprocess_url.return_value = "https://example.com/article1"
            mock_strategy.extract_data.return_value = {
                "title": "Test Article 1",
                "text_content": "Test article content",
                "author": None,
                "publication_date": None,
                "content_type": "html",
                "final_url_after_redirects": "https://example.com/article1",
            }
            mock_strategy.prepare_for_llm.return_value = {
                "content_to_summarize": "Test article content"
            }
            mock_strategy.extract_internal_urls.return_value = []

            mock_registry_instance = Mock()
            mock_registry_instance.get_strategy.return_value = mock_strategy
            mock_registry.return_value = mock_registry_instance

            # Process with sequential processor
            processor = SequentialTaskProcessor()

            # Process single task
            task = queue_service.dequeue(worker_id="test-worker")
            if task:
                result = processor.process_task(TaskEnvelope.from_queue_data(task))
                queue_service.complete_task(
                    task["id"],
                    success=result.success,
                    error_message=result.error_message,
                )

            summarize_task = queue_service.dequeue(
                task_type=TaskType.SUMMARIZE, worker_id="test-worker"
            )
            if summarize_task:
                result = processor.process_task(TaskEnvelope.from_queue_data(summarize_task))
                queue_service.complete_task(
                    summarize_task["id"],
                    success=result.success,
                    error_message=result.error_message,
                )

            # Verify content was processed
            with get_db() as db:
                content = db.query(Content).filter(Content.id == article_id).first()
                source_body = (
                    db.query(ContentBody)
                    .filter(
                        ContentBody.content_id == article_id,
                        ContentBody.variant == "source",
                    )
                    .first()
                )
                assert content.status == ContentStatus.COMPLETED.value
                assert content.processed_at is not None
                assert content.content_metadata.get("summary", {}).get("summary") == "Test summary"
                assert content.content_metadata.get("content") is None
                assert content.content_metadata.get("excerpt")
                assert content.search_text
                assert source_body is not None

    @pytest.mark.integration
    def test_failed_task_retry_mechanism(self, setup_test_db):
        """Test task retry mechanism for failed tasks."""
        failing_article_id = setup_test_db["failing_article_id"]
        queue_service = QueueService()

        # Create task for content that will fail
        queue_service.enqueue(
            task_type=TaskType.PROCESS_CONTENT,
            content_id=failing_article_id,
        )

        with (
            patch("app.pipeline.worker.get_http_service") as mock_http_service,
            patch("app.pipeline.worker.get_strategy_registry") as mock_registry,
        ):
            # Setup to fail
            mock_http = Mock()
            mock_http.fetch_content.side_effect = Exception("Network error")
            mock_http_service.return_value = mock_http

            mock_strategy = Mock()
            mock_strategy.preprocess_url.return_value = "https://failing-site.com/article"

            mock_registry_instance = Mock()
            mock_registry_instance.get_strategy.return_value = mock_strategy
            mock_registry.return_value = mock_registry_instance

            processor = SequentialTaskProcessor()

            # Process the failing task
            task = queue_service.dequeue(worker_id="test-worker")
            assert task is not None

            result = processor.process_task(TaskEnvelope.from_queue_data(task))
            assert result.success is False

            # Check task was marked for retry
            queue_service.complete_task(task["id"], success=False)

            # Should be able to retry
            queue_service.retry_task(task["id"], delay_seconds=0)

            # Check retry count increased
            with get_db() as db:
                updated_task = (
                    db.query(ProcessingTask).filter(ProcessingTask.id == task["id"]).first()
                )
                assert updated_task.retry_count == 1
                assert updated_task.status == "pending"

    @pytest.mark.integration
    def test_concurrent_processing(self, setup_test_db):
        """Test concurrent processing with multiple workers."""
        article1_id = setup_test_db["article1_id"]
        article2_id = setup_test_db["article2_id"]
        queue_service = QueueService()

        # Create multiple tasks
        for content_id in [article1_id, article2_id]:
            queue_service.enqueue(task_type=TaskType.PROCESS_CONTENT, content_id=content_id)

        # Mock services
        with (
            patch("app.pipeline.worker.get_http_service") as mock_http_service,
            patch("app.pipeline.sequential_task_processor.get_llm_service") as mock_llm_service,
            patch("app.pipeline.worker.get_strategy_registry") as mock_registry,
        ):
            # Setup mocks for concurrent article processing
            mock_http = Mock()
            mock_http.fetch_content.return_value = (
                "<html><body>Content</body></html>",
                {"content-type": "text/html"},
            )
            mock_http_service.return_value = mock_http

            mock_llm = Mock()
            mock_summary = _SummaryStub("Summary")
            mock_llm.summarize.return_value = mock_summary
            mock_llm_service.return_value = mock_llm

            # Article strategy
            mock_strategy = Mock()
            mock_strategy.preprocess_url.return_value = "https://example.com/article1"
            mock_strategy.extract_data.return_value = {
                "title": "Article",
                "text_content": "Content",
                "author": None,
                "publication_date": None,
                "content_type": "html",
                "final_url_after_redirects": "https://example.com/article1",
            }
            mock_strategy.prepare_for_llm.return_value = {"content_to_summarize": "Content"}
            mock_strategy.extract_internal_urls.return_value = []

            mock_registry_instance = Mock()
            mock_registry_instance.get_strategy.return_value = mock_strategy
            mock_registry.return_value = mock_registry_instance

            # Process tasks with multiple workers
            processor = SequentialTaskProcessor()

            # Simulate two workers processing in parallel
            worker1_task = queue_service.dequeue(worker_id="worker-1")
            worker2_task = queue_service.dequeue(worker_id="worker-2")

            if worker1_task:
                result = processor.process_task(TaskEnvelope.from_queue_data(worker1_task))
                queue_service.complete_task(
                    worker1_task["id"],
                    success=result.success,
                    error_message=result.error_message,
                )

            if worker2_task:
                result = processor.process_task(TaskEnvelope.from_queue_data(worker2_task))
                queue_service.complete_task(
                    worker2_task["id"],
                    success=result.success,
                    error_message=result.error_message,
                )

            # Verify both tasks were processed
            with get_db() as db:
                completed_tasks = (
                    db.query(ProcessingTask).filter(ProcessingTask.status == "completed").all()
                )
                assert len(completed_tasks) == 2

    @pytest.mark.integration
    def test_pipeline_error_recovery(self, setup_test_db):
        """Test pipeline recovery from various error conditions."""
        article1_id = setup_test_db["article1_id"]
        queue_service = QueueService()

        # Test handling of invalid content ID
        queue_service.enqueue(
            task_type=TaskType.PROCESS_CONTENT,
            content_id=9999,  # Non-existent
        )

        processor = SequentialTaskProcessor()
        task = queue_service.dequeue(worker_id="test-worker")

        result = processor.process_task(TaskEnvelope.from_queue_data(task))
        assert result.success is False

        # Test handling of invalid task type
        with get_db() as db:
            invalid_task = ProcessingTask(
                task_type="INVALID_TYPE",
                payload={"content_id": article1_id},
                status="pending",
                created_at=datetime.now(UTC),
                retry_count=0,
            )
            db.add(invalid_task)
            db.commit()

            task_data = {
                "id": invalid_task.id,
                "task_type": "INVALID_TYPE",
                "payload": invalid_task.payload,
                "retry_count": 0,
            }

            from pydantic import ValidationError

            with pytest.raises(ValidationError):
                TaskEnvelope.from_queue_data(task_data)

    @pytest.mark.integration
    def test_end_to_end_scraping_and_processing(self, setup_test_db):
        """Test complete flow from scraping to processing."""
        del setup_test_db
        queue_service = QueueService()

        # Create scrape task
        queue_service.enqueue(task_type=TaskType.SCRAPE, payload={"sources": ["test"]})

        with patch("app.pipeline.handlers.scrape.ScraperRunner") as mock_runner:
            # Mock scraper to create new content
            def mock_scrape(source):
                with get_db() as db:
                    new_content = Content(
                        url=f"https://scraped.com/{source}/article",
                        title=f"Scraped from {source}",
                        content_type=ContentType.ARTICLE.value,
                        status=ContentStatus.NEW.value,
                        created_at=datetime.now(UTC),
                        content_metadata={},
                    )
                    db.add(new_content)
                    db.commit()

                    # Create processing task for new content
                    queue_service.enqueue(
                        task_type=TaskType.PROCESS_CONTENT, content_id=new_content.id
                    )
                return 1

            mock_runner_instance = Mock()
            mock_runner_instance.run_scraper.side_effect = mock_scrape
            mock_runner.return_value = mock_runner_instance

            processor = SequentialTaskProcessor()

            # Process scrape task
            scrape_task = queue_service.dequeue(worker_id="scraper")
            result = processor.process_task(TaskEnvelope.from_queue_data(scrape_task))
            assert result.success is True

            # Verify new content was created and task queued
            with get_db() as db:
                new_content = db.query(Content).filter(Content.url.like("%scraped.com%")).first()
                assert new_content is not None

                # Check processing task was created
                process_task = (
                    db.query(ProcessingTask)
                    .filter(
                        ProcessingTask.task_type == TaskType.PROCESS_CONTENT.value,
                        ProcessingTask.content_id == new_content.id,
                    )
                    .first()
                )
                assert process_task is not None
