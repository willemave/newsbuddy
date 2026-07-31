"""Tests for the sequential task processor."""

import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.internal.queue import ClaimedTask, TaskTransition
from app.pipeline.queue_notifications import psycopg_conninfo
from app.pipeline.sequential_task_processor import (
    SequentialTaskProcessor,
    _lease_heartbeat_interval_seconds,
    _lease_heartbeat_retry_seconds,
)
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.pipeline.task_specs import TASK_SPECS

CLAIM_TOKEN = UUID("00000000-0000-0000-0000-000000000001")


def _owned_claim(**overrides: object) -> ClaimedTask:
    now = datetime.now(UTC).replace(tzinfo=None)
    task_data: dict[str, object] = {
        "id": 1,
        "task_type": TaskType.SCRAPE.value,
        "content_id": None,
        "payload": {},
        "retry_count": 0,
        "status": TaskStatus.PROCESSING.value,
        "queue_name": TaskQueue.CONTENT.value,
        "created_at": now,
        "available_at": now,
        "started_at": now,
        "locked_at": now,
        "locked_by": "content-processor-1",
        "lease_token": CLAIM_TOKEN,
        "lease_expires_at": now + timedelta(minutes=5),
    }
    task_data.update(overrides)
    return ClaimedTask.model_validate(task_data)


def _transition_for(
    claim: ClaimedTask,
    result: TaskResult,
    *,
    max_retries: int,
) -> TaskTransition:
    """Return a minimal persisted outcome without duplicating QueueService policy."""
    status = (
        TaskStatus.COMPLETED
        if result.success
        else TaskStatus.PENDING
        if result.deferred
        else TaskStatus.FAILED
    )
    return TaskTransition(
        task_type=claim.task_type,
        queue_name=claim.queue_name,
        content_id=claim.content_id,
        error_message=None if result.success or result.deferred else result.error_message,
        status=status,
        retry_count=claim.retry_count,
        retry_delay_seconds=result.retry_delay_seconds if result.deferred else None,
        deferred=result.deferred,
        available_at=claim.available_at,
    )


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
        instance.queue_service.finalize_task.side_effect = _transition_for
        instance.queue_service.renew_lease.return_value = True
        instance.llm_service = Mock()
        instance.dispatcher = Mock()
        return instance


class TestSequentialTaskProcessor:
    """Test cases for SequentialTaskProcessor."""

    def test_psycopg_conninfo_strips_sqlalchemy_driver_suffix(self):
        conninfo = psycopg_conninfo(
            "postgresql+psycopg://newsly:secret@127.0.0.1:5432/newsly?sslmode=prefer"
        )

        assert conninfo == "postgresql://newsly:secret@127.0.0.1:5432/newsly?sslmode=prefer"

    def test_module_import_does_not_eagerly_load_handlers_or_content_worker(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import app.pipeline.sequential_task_processor; "
                    "assert not any(name.startswith('app.pipeline.handlers.') "
                    "for name in sys.modules); "
                    "assert 'app.pipeline.worker' not in sys.modules"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr

    def test_init(self, processor):
        """Test processor initialization."""
        assert processor.running is True
        assert processor.worker_id == "content-processor-1"
        assert processor.queue_service is not None
        assert processor.llm_service is not None

    def test_worker_id_gains_a_thread_suffix_when_threaded(self):
        """Threads in one process must claim under distinct worker ids."""
        with (
            patch("app.pipeline.sequential_task_processor.QueueService") as queue_service_cls,
            patch("app.pipeline.sequential_task_processor.get_llm_service"),
        ):
            queue_service_cls._normalize_queue_name.return_value = "content"

            threaded = SequentialTaskProcessor(thread_index=3)

        assert threaded.worker_id == "content-processor-1-t3"

    @pytest.mark.parametrize("queue", list(TaskQueue))
    def test_handler_composition_matches_queue_task_specs(self, queue):
        """A queue process should expose exactly the task types assigned to it."""
        with patch("app.pipeline.sequential_task_processor.get_llm_service"):
            processor = SequentialTaskProcessor(queue)

        expected_task_types = {
            task_type for task_type, spec in TASK_SPECS.items() if spec.queue == queue
        }
        assert set(processor.dispatcher._handlers) == expected_task_types

    def test_media_queue_does_not_initialize_context_llm_service(self):
        with (
            patch("app.pipeline.sequential_task_processor.get_llm_service") as llm_service,
            patch("app.pipeline.sequential_task_processor.build_handlers_for_queue") as handlers,
        ):
            processor = SequentialTaskProcessor(TaskQueue.MEDIA)

        assert processor.llm_service is None
        llm_service.assert_not_called()
        handlers.assert_called_once_with(TaskQueue.MEDIA.value)

    @pytest.mark.parametrize("queue", [TaskQueue.CONTENT, TaskQueue.DISCUSSION])
    def test_llm_queues_initialize_context_llm_service(self, queue):
        with (
            patch("app.pipeline.sequential_task_processor.get_llm_service") as llm_service,
            patch("app.pipeline.sequential_task_processor.build_handlers_for_queue"),
        ):
            processor = SequentialTaskProcessor(queue)

        assert processor.llm_service is llm_service.return_value
        llm_service.assert_called_once_with()

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

    def test_process_task_normalizes_payload_before_dispatch(self, processor):
        """Task specs apply defaults before a task reaches its handler."""
        task = TaskEnvelope(
            id=1,
            task_type=TaskType.ANALYZE_URL,
            retry_count=0,
            payload={"content_id": 123, "instruction": "read links"},
        )
        processor.dispatcher.dispatch.return_value = TaskResult.ok()

        result = processor.process_task(task)

        assert result.success is True
        dispatched_task = processor.dispatcher.dispatch.call_args.args[0]
        assert dispatched_task.payload == {
            "content_id": 123,
            "instruction": "read links",
            "crawl_links": False,
            "subscribe_to_feed": False,
        }

    def test_process_task_rejects_invalid_spec_payload(self, processor):
        """Malformed spec payloads fail before handler dispatch."""
        task = TaskEnvelope(
            id=1,
            task_type=TaskType.ANALYZE_URL,
            retry_count=0,
            payload={"content_id": "not-an-int"},
        )

        result = processor.process_task(task)

        assert result.success is False
        assert result.retryable is False
        assert "Invalid payload for analyze_url" in (result.error_message or "")
        processor.dispatcher.dispatch.assert_not_called()

    def test_process_task_sets_default_error_message(self, processor):
        """Test default error message when handler returns none."""
        task = TaskEnvelope(
            id=1,
            task_type=TaskType.PROCESS_PODCAST_MEDIA,
            retry_count=0,
            payload={},
        )
        processor.dispatcher.dispatch.return_value = TaskResult(success=False)

        result = processor.process_task(task)

        assert result.success is False
        assert result.error_message == "process_podcast_media returned False"

    def test_claim_rejects_non_object_payload(self):
        with pytest.raises(ValidationError, match="payload must be a JSON object"):
            _owned_claim(payload=["not", "an", "object"])

    def test_lease_heartbeat_timing_stays_inside_short_lease_window(self):
        interval_seconds = _lease_heartbeat_interval_seconds(1)
        retry_seconds = _lease_heartbeat_retry_seconds(0.05)

        assert 0 < interval_seconds < 1
        assert 0 < retry_seconds <= 0.05

    def test_lease_heartbeat_retries_transient_database_error(self, processor):
        claim = _owned_claim()
        renewed = threading.Event()
        attempts = 0

        def renew_lease(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OperationalError("UPDATE lease", {}, Exception("temporary failure"))
            renewed.set()
            return True

        processor.queue_service.renew_lease.side_effect = renew_lease
        with (
            patch(
                "app.pipeline.sequential_task_processor._lease_heartbeat_interval_seconds",
                return_value=0.01,
            ),
            patch(
                "app.pipeline.sequential_task_processor._lease_heartbeat_retry_seconds",
                return_value=0.01,
            ),
            processor._lease_heartbeat(claim) as ownership_lost,
        ):
            assert renewed.wait(timeout=1)
            assert ownership_lost.is_set() is False

        assert attempts >= 2

    def test_lease_heartbeat_surfaces_ownership_loss(self, processor):
        claim = _owned_claim()
        attempted = threading.Event()

        def lose_lease(*_args, **_kwargs):
            attempted.set()
            return False

        processor.queue_service.renew_lease.side_effect = lose_lease
        with (
            patch(
                "app.pipeline.sequential_task_processor._lease_heartbeat_interval_seconds",
                return_value=0.01,
            ),
            processor._lease_heartbeat(claim) as ownership_lost,
        ):
            assert attempted.wait(timeout=1)
            assert ownership_lost.wait(timeout=1)

    def test_handler_lease_renewal_marks_ownership_lost(self, processor):
        claim = _owned_claim()
        ownership_lost = threading.Event()
        processor.queue_service.renew_lease.return_value = False

        context = processor._context_for_claim(claim, ownership_lost)

        assert context.renew_current_lease() is False
        assert ownership_lost.is_set()

    def test_run_processes_tasks_sequentially(self, processor):
        """Test that run method processes tasks sequentially."""
        task1 = _owned_claim(
            id=1,
            task_type=TaskType.SCRAPE.value,
            retry_count=0,
            payload={},
        )
        task2 = _owned_claim(
            id=2,
            task_type=TaskType.PROCESS_CONTENT.value,
            retry_count=0,
            content_id=123,
        )

        processor.queue_service.dequeue.side_effect = [task1, task2, None]
        processor.process_task = Mock(return_value=TaskResult.ok())

        with patch("app.pipeline.sequential_task_processor.setup_logging"):
            processor.run(max_tasks=2)

        assert processor.process_task.call_count == 2
        processor.queue_service.finalize_task.assert_any_call(
            task1,
            TaskResult.ok(),
            max_retries=processor.settings.queue.max_retries,
        )
        processor.queue_service.finalize_task.assert_any_call(
            task2,
            TaskResult.ok(),
            max_retries=processor.settings.queue.max_retries,
        )

    def test_run_forwards_failure_to_queue_service(self, processor):
        task_data = _owned_claim(
            id=1,
            task_type=TaskType.PROCESS_PODCAST_MEDIA.value,
            retry_count=1,
            content_id=789,
        )

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
        processor.settings.queue.max_retries = 3

        with patch("app.pipeline.sequential_task_processor.setup_logging"):
            processor.run()

        processor.queue_service.finalize_task.assert_called_once_with(
            task_data,
            TaskResult.fail("boom"),
            max_retries=3,
        )

    def test_run_does_not_count_success_when_finalization_is_rejected(self, processor):
        task_data = _owned_claim(
            id=1,
            task_type=TaskType.SCRAPE.value,
            retry_count=0,
            payload={},
        )
        call_count = 0

        def mock_dequeue(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return task_data
            processor.running = False
            return None

        processor.queue_service.dequeue.side_effect = mock_dequeue
        processor.queue_service.finalize_task.side_effect = None
        processor.queue_service.finalize_task.return_value = None
        processor.process_task = Mock(return_value=TaskResult.ok())

        with patch("app.pipeline.sequential_task_processor.setup_logging"):
            processed_count = processor.run()

        assert processed_count == 0

    def test_run_single_task_forwards_deferral(self, processor):
        task_data = _owned_claim(
            id=1,
            task_type=TaskType.RUN_LLM_TASK.value,
            retry_count=3,
            payload={"llm_task_id": 7},
        )
        processor.process_task = Mock(return_value=TaskResult.defer(retry_delay_seconds=90))

        with patch("app.pipeline.sequential_task_processor.setup_logging"):
            success = processor.run_single_task(task_data)

        assert success is False
        processor.queue_service.finalize_task.assert_called_once_with(
            task_data,
            TaskResult.defer(retry_delay_seconds=90),
            max_retries=processor.settings.queue.max_retries,
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

    def test_run_single_task(self, processor):
        """Test run_single_task method."""
        task_data = _owned_claim(
            id=1,
            task_type=TaskType.PROCESS_CONTENT.value,
            retry_count=0,
            content_id=123,
        )

        processor.process_task = Mock(return_value=TaskResult.ok())

        with patch("app.pipeline.sequential_task_processor.setup_logging"):
            result = processor.run_single_task(task_data)

        assert result is True
        processor.process_task.assert_called_once()
        processor.queue_service.finalize_task.assert_called_once_with(
            task_data,
            TaskResult.ok(),
            max_retries=processor.settings.queue.max_retries,
        )

    def test_run_single_task_forwards_failure_result(self, processor):
        task_data = _owned_claim(
            id=1,
            task_type=TaskType.PROCESS_PODCAST_MEDIA.value,
            retry_count=0,
            content_id=456,
        )

        processor.process_task = Mock(return_value=TaskResult.fail("boom"))
        processor.settings.queue.max_retries = 3

        with patch("app.pipeline.sequential_task_processor.setup_logging"):
            result = processor.run_single_task(task_data)

        assert result is False
        processor.queue_service.finalize_task.assert_called_once_with(
            task_data,
            TaskResult.fail("boom"),
            max_retries=3,
        )

    def test_run_single_task_with_invalid_payload(self, processor):
        """Test run_single_task handles invalid payloads gracefully."""
        task_data = _owned_claim(id=1, task_type="INVALID_TYPE", retry_count=0)

        with patch("app.pipeline.sequential_task_processor.setup_logging"):
            result = processor.run_single_task(task_data)

        assert result is False
        processor.queue_service.finalize_task.assert_called_once_with(
            task_data,
            TaskResult.fail("Invalid task payload", retryable=False),
            max_retries=processor.settings.queue.max_retries,
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
        task_data = _owned_claim(
            id=1,
            task_type=TaskType.SCRAPE.value,
            retry_count=0,
            payload={},
        )
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

    def test_run_recovers_from_transient_operational_error(self, processor):
        """A transient DB recovery error should reset connections and continue looping."""
        call_count = 0

        def mock_dequeue(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OperationalError(
                    "SELECT processing_tasks.id",
                    {},
                    Exception("FATAL: the database system is in recovery mode"),
                )
            processor.running = False
            return None

        processor.queue_service.dequeue.side_effect = mock_dequeue
        listener = Mock()
        processor._listener = listener

        with (
            patch("app.pipeline.sequential_task_processor.setup_logging"),
            patch("app.pipeline.sequential_task_processor.dispose_db_engine") as mock_dispose,
            patch("time.sleep") as mock_sleep,
        ):
            processor.run()

        assert call_count == 2
        mock_dispose.assert_called_once()
        listener.reset.assert_called()
        mock_sleep.assert_any_call(10.0)
