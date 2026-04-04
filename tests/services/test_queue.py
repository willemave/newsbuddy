import sqlite3
from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock, patch

import pytest
from sqlalchemy.exc import OperationalError

from app.models.schema import ProcessingTask
from app.services.queue import QueueService, TaskStatus, TaskType, get_queue_service


def _set_last_added_task_id(mock_db_session, task_id: int) -> None:
    mock_db_session.add.call_args[0][0].id = task_id


class TestQueueService:
    """Test the QueueService class."""

    @pytest.fixture
    def mock_db_session(self):
        """Fixture for mocked database session."""
        with patch('app.services.queue.get_db') as mock_get_db:
            mock_session = MagicMock()
            default_query = MagicMock()
            default_query.filter.return_value = default_query
            default_query.order_by.return_value = default_query
            default_query.first.return_value = None
            mock_session.query.return_value = default_query
            mock_get_db.return_value.__enter__.return_value = mock_session
            yield mock_session

    def test_enqueue_task(self, mock_db_session):
        """Test enqueueing a new task."""
        mock_db_session.flush.side_effect = lambda: _set_last_added_task_id(mock_db_session, 123)

        service = QueueService()
        task_id = service.enqueue(
            task_type=TaskType.PROCESS_CONTENT,
            content_id=456,
            payload={'test': 'data'}
        )

        # Verify task was created correctly
        assert task_id == 123
        mock_db_session.add.assert_called_once()
        mock_db_session.flush.assert_called_once()
        mock_db_session.commit.assert_called_once()

        # Verify task attributes
        added_task = mock_db_session.add.call_args[0][0]
        assert added_task.task_type == TaskType.PROCESS_CONTENT.value
        assert added_task.content_id == 456
        assert added_task.payload == {'test': 'data'}
        assert added_task.status == TaskStatus.PENDING.value

    def test_enqueue_task_minimal(self, mock_db_session):
        """Test enqueueing a task with minimal parameters."""
        mock_db_session.flush.side_effect = lambda: _set_last_added_task_id(mock_db_session, 789)

        service = QueueService()
        task_id = service.enqueue(task_type=TaskType.SCRAPE)

        assert task_id == 789
        added_task = mock_db_session.add.call_args[0][0]
        assert added_task.task_type == TaskType.SCRAPE.value
        assert added_task.content_id is None
        assert added_task.payload == {}

    def test_dequeue_task_success(self, mock_db_session):
        """Test successful task dequeue."""
        # Mock task in database
        mock_task = ProcessingTask()
        mock_task.id = 123
        mock_task.task_type = TaskType.PROCESS_CONTENT.value
        mock_task.content_id = 456
        mock_task.payload = {'test': 'data'}
        mock_task.retry_count = 0
        mock_task.status = TaskStatus.PENDING.value
        mock_task.created_at = datetime.now(UTC)

        # Mock query chain
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.with_for_update.return_value = mock_query
        mock_query.first.return_value = mock_task
        mock_db_session.query.return_value = mock_query

        service = QueueService()
        result = service.dequeue(worker_id="test-worker")

        # Verify result
        assert result is not None
        assert result['id'] == 123
        assert result['task_type'] == TaskType.PROCESS_CONTENT.value
        assert result['content_id'] == 456
        assert result['payload'] == {'test': 'data'}
        assert result['retry_count'] == 0

        # Verify task claim/update path was executed.
        assert mock_db_session.query.return_value.filter.return_value.update.called
        mock_db_session.commit.assert_called_once()

    def test_dequeue_task_empty_queue(self, mock_db_session):
        """Test dequeue when queue is empty."""
        # Mock empty query result
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.with_for_update.return_value = mock_query
        mock_query.first.return_value = None
        mock_db_session.query.return_value = mock_query

        service = QueueService()
        result = service.dequeue()

        assert result is None
        mock_db_session.commit.assert_not_called()

    def test_dequeue_task_with_type_filter(self, mock_db_session):
        """Test dequeue with task type filter."""
        mock_task = ProcessingTask()
        mock_task.id = 123

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.with_for_update.return_value = mock_query
        mock_query.first.return_value = mock_task
        mock_db_session.query.return_value = mock_query

        service = QueueService()
        service.dequeue(task_type=TaskType.DOWNLOAD_AUDIO)

        # Verify filter was applied for task type
        filter_calls = mock_query.filter.call_args_list
        assert len(filter_calls) >= 2

    def test_complete_task_success(self, mock_db_session):
        """Test marking task as completed successfully."""
        mock_task = ProcessingTask()
        mock_task.id = 123
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_task

        service = QueueService()
        service.complete_task(task_id=123, success=True)

        assert mock_task.status == TaskStatus.COMPLETED.value
        assert mock_task.completed_at is not None
        assert mock_task.error_message is None
        mock_db_session.commit.assert_called_once()

    def test_complete_task_failure(self, mock_db_session):
        """Test marking task as failed."""
        mock_task = ProcessingTask()
        mock_task.id = 123
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_task

        service = QueueService()
        service.complete_task(
            task_id=123,
            success=False,
            error_message="Test error"
        )

        assert mock_task.status == TaskStatus.FAILED.value
        assert mock_task.completed_at is not None
        assert mock_task.error_message == "Test error"
        mock_db_session.commit.assert_called_once()

    def test_complete_task_not_found(self, mock_db_session):
        """Test completing a task that doesn't exist."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        service = QueueService()
        service.complete_task(task_id=999)

        # Should not crash, just log error
        mock_db_session.commit.assert_not_called()

    def test_retry_task(self, mock_db_session):
        """Test retrying a failed task."""
        mock_task = ProcessingTask()
        mock_task.id = 123
        mock_task.retry_count = 1
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_task

        service = QueueService()
        service.retry_task(task_id=123, delay_seconds=120)

        assert mock_task.status == TaskStatus.PENDING.value
        assert mock_task.retry_count == 2
        assert mock_task.started_at is None
        assert mock_task.completed_at is None
        # created_at should be in the future
        assert mock_task.created_at > datetime.now(UTC)
        mock_db_session.commit.assert_called_once()

    def test_retry_task_not_found(self, mock_db_session):
        """Test retrying a task that doesn't exist."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        service = QueueService()
        service.retry_task(task_id=999)

        mock_db_session.commit.assert_not_called()

    def test_finalize_task_schedules_retry(self, mock_db_session):
        """Retryable failures transition back to pending in one write."""
        mock_task = ProcessingTask()
        mock_task.id = 123
        mock_task.retry_count = 1
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_task

        service = QueueService()
        transition = service.finalize_task(
            123,
            success=False,
            error_message="boom",
            retryable=True,
            current_retry_count=1,
            max_retries=3,
            retry_delay_seconds=120,
        )

        assert transition is not None
        assert transition["status"] == TaskStatus.PENDING.value
        assert transition["retry_count"] == 2
        assert transition["retry_delay_seconds"] == 120
        assert mock_task.status == TaskStatus.PENDING.value
        assert mock_task.retry_count == 2
        assert mock_task.started_at is None
        assert mock_task.completed_at is None
        assert mock_task.error_message == "boom"
        assert mock_task.created_at > datetime.now(UTC)
        mock_db_session.commit.assert_called_once()

    def test_get_queue_stats(self, mock_db_session):
        """Test getting queue statistics."""
        status_query = Mock()
        status_query.group_by.return_value.all.return_value = [
            ("pending", 5),
            ("processing", 2),
            ("completed", 10),
        ]

        type_query = Mock()
        type_query.filter.return_value.group_by.return_value.all.return_value = [
            ("process_content", 3),
            ("download_audio", 2),
        ]

        queue_query = Mock()
        queue_query.filter.return_value.group_by.return_value.all.return_value = [
            ("content", 4),
            ("media", 1),
        ]

        queue_type_query = Mock()
        queue_type_query.filter.return_value.group_by.return_value.all.return_value = [
            ("content", "process_content", 3),
            ("content", "download_audio", 1),
            ("media", "transcribe", 1),
        ]

        failure_query = Mock()
        failure_query.filter.return_value.scalar.return_value = 1

        mock_db_session.query.side_effect = [
            status_query,
            type_query,
            queue_query,
            queue_type_query,
            failure_query,
        ]

        service = QueueService()
        stats = service.get_queue_stats()

        expected_stats = {
            'by_status': {'pending': 5, 'processing': 2, 'completed': 10},
            'pending_by_type': {'process_content': 3, 'download_audio': 2},
            "pending_by_queue": {"content": 4, "media": 1},
            "pending_by_queue_type": {
                "content": {
                    "process_content": 3,
                    "download_audio": 1,
                },
                "media": {
                    "transcribe": 1,
                },
            },
            'recent_failures': 1,
        }

        assert stats == expected_stats

    def test_cleanup_old_tasks(self, mock_db_session):
        """Test cleaning up old completed tasks."""
        mock_db_session.query.return_value.filter.return_value.delete.return_value = 5

        service = QueueService()
        service.cleanup_old_tasks(days=30)

        mock_db_session.commit.assert_called_once()
        # Verify delete was called
        mock_db_session.query.return_value.filter.return_value.delete.assert_called_once()


class TestTaskEnums:
    """Test task-related enums."""

    def test_task_type_values(self):
        """Test TaskType enum values."""
        assert TaskType.SCRAPE.value == "scrape"
        assert TaskType.ANALYZE_URL.value == "analyze_url"
        assert TaskType.PROCESS_CONTENT.value == "process_content"
        assert TaskType.DOWNLOAD_AUDIO.value == "download_audio"
        assert TaskType.TRANSCRIBE.value == "transcribe"
        assert TaskType.SUMMARIZE.value == "summarize"
        assert TaskType.GENERATE_IMAGE.value == "generate_image"
        assert TaskType.DISCOVER_FEEDS.value == "discover_feeds"
        assert TaskType.DIG_DEEPER.value == "dig_deeper"
        assert TaskType.GENERATE_NEWS_DIGEST.value == "generate_news_digest"
        assert TaskType.SYNC_INTEGRATION.value == "sync_integration"

    def test_task_status_values(self):
        """Test TaskStatus enum values."""
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.PROCESSING.value == "processing"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"

    def test_task_status_count(self):
        """TaskStatus should stay intentionally small and stable."""
        assert len(list(TaskStatus)) == 4


class TestQueueServiceSingleton:
    """Test the global queue service instance."""

    def test_get_queue_service_singleton(self):
        """Test that get_queue_service returns the same instance."""
        service1 = get_queue_service()
        service2 = get_queue_service()

        assert service1 is service2
        assert isinstance(service1, QueueService)

    @patch('app.services.queue._queue_service', None)
    def test_get_queue_service_creates_instance(self):
        """Test that get_queue_service creates instance when needed."""
        service = get_queue_service()
        assert isinstance(service, QueueService)


class TestQueueServiceIntegration:
    """Integration tests for queue operations."""

    @pytest.fixture
    def mock_db_session(self):
        """Fixture for mocked database session."""
        with patch('app.services.queue.get_db') as mock_get_db:
            mock_session = MagicMock()
            default_query = MagicMock()
            default_query.filter.return_value = default_query
            default_query.order_by.return_value = default_query
            default_query.first.return_value = None
            mock_session.query.return_value = default_query
            mock_get_db.return_value.__enter__.return_value = mock_session
            yield mock_session

    def test_enqueue_dequeue_complete_workflow(self, mock_db_session):
        """Test complete workflow: enqueue -> dequeue -> complete."""
        service = QueueService()

        mock_db_session.flush.side_effect = lambda: _set_last_added_task_id(mock_db_session, 123)

        # Enqueue task
        task_id = service.enqueue(
            task_type=TaskType.PROCESS_CONTENT,
            content_id=456
        )
        assert task_id == 123

        # Mock task for dequeue
        mock_task = ProcessingTask()
        mock_task.id = 123
        mock_task.task_type = TaskType.PROCESS_CONTENT.value
        mock_task.content_id = 456
        mock_task.status = TaskStatus.PENDING.value
        mock_task.created_at = datetime.now(UTC)

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.with_for_update.return_value = mock_query
        mock_query.first.return_value = mock_task
        mock_db_session.query.return_value = mock_query

        # Dequeue task
        dequeued = service.dequeue()
        assert dequeued['id'] == 123
        assert dequeued['task_type'] == TaskType.PROCESS_CONTENT.value

        # Mock task for completion
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_task

        # Complete task
        service.complete_task(task_id=123, success=True)
        assert mock_task.status == TaskStatus.COMPLETED.value

    def test_enqueue_dequeue_retry_workflow(self, mock_db_session):
        """Test workflow: enqueue -> dequeue -> fail -> retry."""
        service = QueueService()

        # Mock task
        mock_task = ProcessingTask()
        mock_task.id = 123
        mock_task.retry_count = 0

        # Mock for complete_task and retry_task
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_task

        # Complete with failure
        service.complete_task(
            task_id=123,
            success=False,
            error_message="Network error"
        )
        assert mock_task.status == TaskStatus.FAILED.value

        # Retry task
        service.retry_task(task_id=123)
        assert mock_task.status == TaskStatus.PENDING.value
        assert mock_task.retry_count == 1

    def test_enqueue_retries_sqlite_lock(self, mock_db_session):
        """SQLite lock contention on enqueue should be retried."""
        mock_db_session.flush.side_effect = lambda: _set_last_added_task_id(mock_db_session, 123)
        mock_db_session.commit.side_effect = [
            OperationalError("INSERT task", {}, sqlite3.OperationalError("database is locked")),
            None,
        ]

        service = QueueService()
        task_id = service.enqueue(task_type=TaskType.PROCESS_CONTENT, content_id=456)

        assert task_id == 123
        assert mock_db_session.commit.call_count == 2
        mock_db_session.rollback.assert_called_once()

    def test_enqueue_does_not_retry_non_lock_operational_error(self, mock_db_session):
        """Non-lock OperationalError cases should fail immediately."""
        mock_db_session.flush.side_effect = lambda: _set_last_added_task_id(mock_db_session, 123)
        mock_db_session.commit.side_effect = OperationalError(
            "INSERT task",
            {},
            sqlite3.OperationalError("disk I/O error"),
        )

        service = QueueService()

        with pytest.raises(OperationalError):
            service.enqueue(task_type=TaskType.PROCESS_CONTENT, content_id=456)

        assert mock_db_session.commit.call_count == 1
        mock_db_session.rollback.assert_not_called()

    def test_complete_task_retries_sqlite_lock(self, mock_db_session):
        """SQLite lock contention on completion should be retried."""
        mock_task = ProcessingTask()
        mock_task.id = 123
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_task
        mock_db_session.commit.side_effect = [
            OperationalError("UPDATE task", {}, sqlite3.OperationalError("database is locked")),
            None,
        ]

        service = QueueService()
        service.complete_task(task_id=123, success=True)

        assert mock_task.status == TaskStatus.COMPLETED.value
        assert mock_db_session.commit.call_count == 2
        mock_db_session.rollback.assert_called_once()

    def test_retry_task_retries_sqlite_lock(self, mock_db_session):
        """SQLite lock contention on retry scheduling should be retried."""
        mock_task = ProcessingTask()
        mock_task.id = 123
        mock_task.retry_count = 1
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_task
        mock_db_session.commit.side_effect = [
            OperationalError("UPDATE task", {}, sqlite3.OperationalError("database is locked")),
            None,
        ]

        service = QueueService()
        service.retry_task(task_id=123, delay_seconds=120)

        assert mock_task.status == TaskStatus.PENDING.value
        assert mock_db_session.commit.call_count == 2
        mock_db_session.rollback.assert_called_once()

    def test_finalize_task_retries_sqlite_lock(self, mock_db_session):
        """SQLite lock contention on finalize should be retried."""
        mock_task = ProcessingTask()
        mock_task.id = 123
        mock_task.retry_count = 0
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_task
        mock_db_session.commit.side_effect = [
            OperationalError("UPDATE task", {}, sqlite3.OperationalError("database is locked")),
            None,
        ]

        service = QueueService()
        transition = service.finalize_task(
            123,
            success=False,
            error_message="boom",
            retryable=True,
            current_retry_count=0,
            max_retries=3,
            retry_delay_seconds=60,
        )

        assert transition is not None
        assert transition["status"] == TaskStatus.PENDING.value
        assert mock_task.status == TaskStatus.PENDING.value
        assert mock_db_session.commit.call_count == 2
        mock_db_session.rollback.assert_called_once()
