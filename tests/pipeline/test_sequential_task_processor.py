"""Tests for the sequential task processor."""

from unittest.mock import Mock, patch

import pytest
from sqlalchemy.exc import OperationalError

from app.pipeline.sequential_task_processor import SequentialTaskProcessor, _psycopg_conninfo
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.queue import TaskType


@pytest.fixture
def processor():
    """Create a processor instance for testing."""
    with (
        patch("app.pipeline.sequential_task_processor.QueueService") as mock_queue_service_cls,
        patch("app.pipeline.sequential_task_processor.get_llm_service"),
    ):
        mock_queue_service_cls._normalize_queue_name.return_value = "content"
        instance = SequentialTaskProcessor()
        instance.queue_service = Mock()
        instance.llm_service = Mock()
        instance.dispatcher = Mock()
        return instance


class TestSequentialTaskProcessor:
    """Test cases for SequentialTaskProcessor."""

    def test_psycopg_conninfo_strips_sqlalchemy_driver_suffix(self):
        conninfo = _psycopg_conninfo(
            "postgresql+psycopg://newsly:secret@127.0.0.1:5432/newsly?sslmode=prefer"
        )

        assert conninfo == "postgresql://newsly:secret@127.0.0.1:5432/newsly?sslmode=prefer"

    def test_init(self, processor):
        """Test processor initialization."""
        assert processor.running is True
        assert processor.worker_id == "content-processor-1"
        assert processor.queue_service is not None
        assert processor.llm_service is not None

    def test_ensure_queue_listener_uses_psycopg_compatible_conninfo(self, processor):
        listener = Mock()
        processor.settings.database_url = "postgresql+psycopg://postgres@localhost/newsly"

        with patch("app.pipeline.sequential_task_processor.psycopg") as mock_psycopg:
            mock_psycopg.connect.return_value = listener

            result = processor._ensure_queue_listener()

        assert result is listener
        mock_psycopg.connect.assert_called_once_with(
            "postgresql://postgres@localhost/newsly",
            autocommit=True,
        )
        listener.execute.assert_called_once_with("LISTEN processing_tasks")

    def test_process_task_dispatches(self, processor):
        """Test processing uses dispatcher and returns TaskResult."""
        task = TaskEnvelope(
            id=1,
            task_type=TaskType.SCRAPE,
            retry_count=0,
            payload={},
        )
        processor.dispatcher.dispatch.return_value = TaskResult.ok()

        result = processor.process_task(task)

        assert result.success is True
        processor.dispatcher.dispatch.assert_called_once()

    def test_process_task_sets_default_error_message(self, processor):
        """Test default error message when handler returns none."""
        task = TaskEnvelope(
            id=1,
            task_type=TaskType.DOWNLOAD_AUDIO,
            retry_count=0,
            payload={},
        )
        processor.dispatcher.dispatch.return_value = TaskResult(success=False)

        result = processor.process_task(task)

        assert result.success is False
        assert result.error_message == "download_audio returned False"

    def test_run_processes_tasks_sequentially(self, processor):
        """Test that run method processes tasks sequentially."""
        task1 = {"id": 1, "task_type": TaskType.SCRAPE.value, "retry_count": 0, "payload": {}}
        task2 = {
            "id": 2,
            "task_type": TaskType.PROCESS_CONTENT.value,
            "retry_count": 0,
            "content_id": 123,
        }

        processor.queue_service.dequeue.side_effect = [task1, task2, None]
        processor.process_task = Mock(return_value=TaskResult.ok())

        with patch("app.pipeline.sequential_task_processor.setup_logging"):
            processor.run(max_tasks=2)

        assert processor.process_task.call_count == 2
        processor.queue_service.finalize_task.assert_any_call(
            1,
            success=True,
            error_message=None,
            retryable=True,
            current_retry_count=0,
            max_retries=processor.settings.max_retries,
            retry_delay_seconds=None,
        )
        processor.queue_service.finalize_task.assert_any_call(
            2,
            success=True,
            error_message=None,
            retryable=True,
            current_retry_count=0,
            max_retries=processor.settings.max_retries,
            retry_delay_seconds=None,
        )

    def test_run_retry_logic(self, processor):
        """Test retry logic for failed tasks."""
        task_data = {
            "id": 1,
            "task_type": TaskType.DOWNLOAD_AUDIO.value,
            "retry_count": 1,
            "content_id": 789,
        }

        call_count = 0

        def mock_dequeue(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return task_data
            processor.running = False
            return None

        processor.queue_service.dequeue.side_effect = mock_dequeue
        processor.process_task = Mock(return_value=TaskResult.fail("boom"))
        processor.settings.max_retries = 3

        with patch("app.pipeline.sequential_task_processor.setup_logging"):
            processor.run()

        processor.queue_service.finalize_task.assert_called_once_with(
            1,
            success=False,
            error_message="boom",
            retryable=True,
            current_retry_count=1,
            max_retries=3,
            retry_delay_seconds=120,
        )

    def test_run_max_retries_exceeded(self, processor):
        """Test behavior when max retries exceeded."""
        task_data = {
            "id": 1,
            "task_type": TaskType.TRANSCRIBE.value,
            "retry_count": 3,
            "content_id": 999,
        }

        call_count = 0

        def mock_dequeue(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return task_data
            processor.running = False
            return None

        processor.queue_service.dequeue.side_effect = mock_dequeue
        processor.process_task = Mock(return_value=TaskResult.fail("boom"))
        processor.settings.max_retries = 3

        with patch("app.pipeline.sequential_task_processor.setup_logging"):
            processor.run()

        processor.queue_service.finalize_task.assert_called_once_with(
            1,
            success=False,
            error_message="boom",
            retryable=True,
            current_retry_count=3,
            max_retries=3,
            retry_delay_seconds=None,
        )

    def test_run_empty_queue_backoff(self, processor):
        """Test backoff behavior when queue is empty."""
        call_count = 0

        def mock_dequeue(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count > 20:
                processor.running = False
            return None

        processor.queue_service.dequeue.side_effect = mock_dequeue

        with (
            patch("app.pipeline.sequential_task_processor.setup_logging"),
            patch("time.sleep") as mock_sleep,
        ):
            processor.run()

        assert mock_sleep.called
        mock_sleep.assert_any_call(0.1)

    def test_run_signal_handler(self, processor):
        """Test signal handler setup."""
        with (
            patch("signal.signal") as mock_signal,
            patch("app.pipeline.sequential_task_processor.setup_logging"),
        ):
            processor.running = False
            processor.run()

            assert mock_signal.call_count >= 2

    def test_run_single_task(self, processor):
        """Test run_single_task method."""
        task_data = {
            "id": 1,
            "task_type": TaskType.PROCESS_CONTENT.value,
            "retry_count": 0,
            "content_id": 123,
        }

        processor.process_task = Mock(return_value=TaskResult.ok())

        with patch("app.pipeline.sequential_task_processor.setup_logging"):
            result = processor.run_single_task(task_data)

        assert result is True
        processor.process_task.assert_called_once()
        processor.queue_service.finalize_task.assert_called_once_with(
            1,
            success=True,
            error_message=None,
            retryable=True,
            current_retry_count=0,
            max_retries=processor.settings.max_retries,
            retry_delay_seconds=None,
        )

    def test_run_single_task_with_retry(self, processor):
        """Test run_single_task with failed task that should retry."""
        task_data = {
            "id": 1,
            "task_type": TaskType.DOWNLOAD_AUDIO.value,
            "retry_count": 0,
            "content_id": 456,
        }

        processor.process_task = Mock(return_value=TaskResult.fail("boom"))
        processor.settings.max_retries = 3

        with patch("app.pipeline.sequential_task_processor.setup_logging"):
            result = processor.run_single_task(task_data)

        assert result is False
        processor.queue_service.finalize_task.assert_called_once_with(
            1,
            success=False,
            error_message="boom",
            retryable=True,
            current_retry_count=0,
            max_retries=3,
            retry_delay_seconds=60,
        )

    def test_run_single_task_with_invalid_payload(self, processor):
        """Test run_single_task handles invalid payloads gracefully."""
        task_data = {
            "id": 1,
            "task_type": "INVALID_TYPE",
            "retry_count": 0,
        }

        with patch("app.pipeline.sequential_task_processor.setup_logging"):
            result = processor.run_single_task(task_data)

        assert result is False
        processor.queue_service.finalize_task.assert_called_once_with(
            1,
            success=False,
            error_message="Invalid task payload",
            retryable=False,
            current_retry_count=0,
            max_retries=processor.settings.max_retries,
            retry_delay_seconds=None,
        )

    def test_process_task_exception_handling(self, processor):
        """Test exception handling in process_task."""
        task = TaskEnvelope(
            id=1,
            task_type=TaskType.SCRAPE,
            retry_count=0,
            payload={"sources": ["all"]},
        )

        processor.dispatcher.dispatch.side_effect = Exception("Test error")

        result = processor.process_task(task)
        assert result.success is False

    def test_run_main_loop_exception_handling(self, processor):
        """Test exception handling in main loop."""
        call_count = 0

        def mock_dequeue_with_error(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                processor.running = False
                return None
            raise Exception("Queue error")

        processor.queue_service.dequeue.side_effect = mock_dequeue_with_error

        with patch("app.pipeline.sequential_task_processor.setup_logging"), patch("time.sleep"):
            processor.run()

        assert processor.running is False
        assert call_count > 2

    def test_run_ignores_task_finalization_lock_error(self, processor):
        """A finalization lock error should not crash the worker loop."""
        task_data = {"id": 1, "task_type": TaskType.SCRAPE.value, "retry_count": 0, "payload": {}}
        call_count = 0

        def mock_dequeue(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return task_data
            processor.running = False
            return None

        processor.queue_service.dequeue.side_effect = mock_dequeue
        processor.queue_service.finalize_task.side_effect = OperationalError(
            "UPDATE task",
            {},
            Exception("database is locked"),
        )
        processor.process_task = Mock(return_value=TaskResult.fail("boom"))

        with patch("app.pipeline.sequential_task_processor.setup_logging"):
            processor.run()

        assert processor.process_task.call_count == 1
        processor.queue_service.finalize_task.assert_called_once()
