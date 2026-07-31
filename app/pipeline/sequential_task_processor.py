"""One queue claim loop: dequeue, process, finalize, idle, repeat.

Process-wide concerns - signal handling, the LISTEN connection, model warmups -
belong to whoever runs these loops (see `threaded_task_processor.py`).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.core.db import dispose_db_engine
from app.core.logging import get_logger, setup_logging
from app.core.observability import bound_log_context, build_log_extra, get_task_event_name
from app.core.settings import get_settings
from app.models.internal.queue import ClaimedTask, TaskTransition
from app.pipeline.dispatcher import TaskDispatcher
from app.pipeline.handler_registry import build_handlers_for_queue
from app.pipeline.queue_notifications import QueueNotificationListener
from app.pipeline.task_context import TaskContext
from app.pipeline.task_handler import TaskHandler
from app.pipeline.task_models import TaskEnvelope, TaskResult, task_will_retry
from app.pipeline.task_specs import TASK_SPECS, get_task_spec
from app.services.gateways.task_queue_gateway import TaskQueueGateway
from app.services.langfuse_tracing import langfuse_trace_context
from app.services.queue import QueueService, TaskQueue, TaskType

if TYPE_CHECKING:
    from app.services.llm_summarization import ContentSummarizer

logger = get_logger(__name__)

_TRANSIENT_DATABASE_ERROR_SNIPPETS = (
    "database system is in recovery mode",
    "server closed the connection unexpectedly",
    "terminating connection due to administrator command",
)


def get_llm_service() -> ContentSummarizer:
    """Load the content summarizer only in queues whose handlers consume it."""
    from app.pipeline.worker import get_llm_service as build_llm_service

    return build_llm_service()


def _queue_uses_context_llm_service(queue_name: str) -> bool:
    return any(
        task_spec.queue.value == queue_name and task_spec.requires_context_llm_service
        for task_spec in TASK_SPECS.values()
    )


def _task_extra(
    task: TaskEnvelope | None,
    *,
    processor: SequentialTaskProcessor,
    operation: str,
    event_name: str | None = None,
    status: str | None = None,
    duration_ms: float | None = None,
    context_data: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build structured logger metadata for a queue task."""
    task_type = task.task_type.value if task else None
    return build_log_extra(
        component="task_processor",
        operation=operation,
        event_name=event_name or get_task_event_name(task_type),
        status=status,
        duration_ms=duration_ms,
        task_id=task.id if task else None,
        task_type=task_type,
        queue_name=processor.queue_name,
        worker_id=processor.worker_id,
        content_id=task.content_id if task else None,
        source="queue",
        context_data=context_data,
    )


def _is_transient_database_operational_error(exc: OperationalError) -> bool:
    """Return whether an OperationalError looks like a transient DB restart/recovery."""
    fragments = [str(exc).lower()]
    original = getattr(exc, "orig", None)
    if original is not None:
        fragments.append(str(original).lower())
    combined = " ".join(fragment for fragment in fragments if fragment)
    return any(snippet in combined for snippet in _TRANSIENT_DATABASE_ERROR_SNIPPETS)


def _task_data_value(
    task_data: ClaimedTask | Mapping[str, Any],
    key: str,
) -> Any:
    """Read one claim field from the typed runtime shape or a test mapping."""
    if isinstance(task_data, ClaimedTask):
        return getattr(task_data, key, None)
    return task_data.get(key)


def _task_data_for_log(task_data: ClaimedTask | Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-compatible task summary for validation failure logs."""
    if isinstance(task_data, ClaimedTask):
        return task_data.model_dump(mode="json")
    return dict(task_data)


def _invalid_task_envelope(
    task_data: ClaimedTask | Mapping[str, Any],
    *,
    task_id: int,
) -> TaskEnvelope:
    """Preserve claim ownership while terminally failing an invalid task type."""
    return TaskEnvelope(
        id=task_id,
        task_type=TaskType.SCRAPE,
        retry_count=int(_task_data_value(task_data, "retry_count") or 0),
        payload={},
        locked_by=_task_data_value(task_data, "locked_by"),
        lease_token=_task_data_value(task_data, "lease_token"),
        lease_expires_at=_task_data_value(task_data, "lease_expires_at"),
    )


class SequentialTaskProcessor:
    """One claim loop: processes tasks one at a time under a single worker id."""

    def __init__(
        self,
        queue_name: TaskQueue | str = TaskQueue.CONTENT,
        worker_slot: int = 1,
        *,
        thread_index: int | None = None,
        notification_listener: QueueNotificationListener | None = None,
    ) -> None:
        logger.debug("Initializing SequentialTaskProcessor...")
        self.queue_service = QueueService()
        self.queue_gateway = TaskQueueGateway(queue_service=self.queue_service)
        logger.debug("QueueService initialized")
        self.settings = get_settings()
        logger.debug("Settings loaded")
        self.queue_name = QueueService._normalize_queue_name(queue_name) or TaskQueue.CONTENT.value
        self.llm_service = (
            get_llm_service() if _queue_uses_context_llm_service(self.queue_name) else None
        )
        if self.llm_service is not None:
            logger.debug("Shared summarization service initialized")
        self.running = True
        self.worker_slot = worker_slot
        # Threads within a process must claim under distinct worker ids so that
        # locked_by and lease renewal stay per-claim. A lone thread keeps the
        # historical id so --threads 1 is an exact rollback.
        thread_suffix = "" if thread_index is None else f"-t{thread_index}"
        self.worker_id = f"{self.queue_name}-processor-{self.worker_slot}{thread_suffix}"
        # Waited on, never started or closed here: the process that owns the
        # listener owns its lifecycle. An unstarted listener simply reports no
        # notifications, and the loop falls back to polling.
        self._listener = notification_listener or QueueNotificationListener(
            str(self.settings.database_url)
        )
        logger.debug(
            "SequentialTaskProcessor initialized with worker_id: %s queue=%s",
            self.worker_id,
            self.queue_name,
        )
        self.context = TaskContext(
            queue_service=self.queue_service,
            settings=self.settings,
            llm_service=self.llm_service,
            worker_id=self.worker_id,
            queue_gateway=self.queue_gateway,
        )
        self.dispatcher = TaskDispatcher(self._build_handlers())

    def _build_handlers(self) -> list[TaskHandler]:
        """Build only the task handlers assigned to this processor's queue."""
        return build_handlers_for_queue(self.queue_name)

    def _idle_wait(self, timeout_seconds: float) -> None:
        """Sleep until the next poll interval or an incoming queue notification."""
        if timeout_seconds <= 0:
            return

        if self._listener.wait(timeout_seconds) is not None:
            return

        time.sleep(timeout_seconds)

    def _recover_from_operational_error(self, exc: OperationalError) -> float:
        """Dispose pooled DB state and drop the listener after a DB operational error."""
        transient = _is_transient_database_operational_error(exc)
        dispose_db_engine()
        self._listener.reset()
        logger_method = logger.warning if transient else logger.error
        logger_method(
            "Database operational error in worker loop; resetting DB connections",
            exc_info=True,
            extra=build_log_extra(
                component="task_processor",
                operation="worker_loop",
                event_name="task.worker_loop_db_error",
                status="degraded" if transient else "failed",
                queue_name=self.queue_name,
                worker_id=self.worker_id,
                source="queue",
                context_data={
                    "failure_class": type(exc).__name__,
                    "transient": transient,
                },
            ),
        )
        return 10.0 if transient else 5.0

    @contextmanager
    def _lease_heartbeat(self, task: TaskEnvelope):
        """Renew the lease for the current task while it is being processed."""
        if (
            not isinstance(self.queue_service, QueueService)
            or task.lease_token is None
            or task.locked_by is None
        ):
            yield
            return
        claim_worker_id = task.locked_by
        lease_token = task.lease_token

        raw_lease_seconds = self.settings.queue.worker_timeout_seconds
        try:
            lease_seconds = max(int(raw_lease_seconds), 1)
        except (TypeError, ValueError):
            lease_seconds = 300
        interval_seconds = max(min(lease_seconds / 3, 30.0), 5.0)
        stop_event = threading.Event()

        def _run() -> None:
            while not stop_event.wait(interval_seconds):
                renewed = self.queue_service.renew_lease(
                    task.id,
                    worker_id=claim_worker_id,
                    lease_token=lease_token,
                    lease_seconds=lease_seconds,
                )
                if renewed:
                    continue
                logger.warning(
                    "Task lease heartbeat stopped after renewal failure",
                    extra=build_log_extra(
                        component="task_processor",
                        operation="renew_lease",
                        event_name="task.lease_heartbeat_stopped",
                        status="degraded",
                        task_id=task.id,
                        worker_id=self.worker_id,
                    ),
                )
                return

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop_event.set()
            thread.join(timeout=1.0)

    def _process_and_finalize_task(
        self,
        task: TaskEnvelope,
    ) -> tuple[TaskResult, dict[str, object] | None]:
        """Run one task under a lease heartbeat and persist the outcome."""
        with self._lease_heartbeat(task):
            result = self.process_task(task)
            finalization = self._finalize_processed_task(task=task, result=result)
        return result, finalization

    def process_task(self, task: TaskEnvelope) -> TaskResult:
        """Process a single task."""
        start_time = time.perf_counter()
        try:
            normalized_payload = get_task_spec(task.task_type).normalize_payload(task.payload)
        except ValueError as exc:
            logger.error(
                "Task payload failed spec validation",
                extra=_task_extra(
                    task,
                    processor=self,
                    operation="validate_task_payload",
                    event_name="task.invalid_payload",
                    status="failed",
                    context_data={
                        "failure_class": type(exc).__name__,
                        "payload_keys": sorted(task.payload.keys()),
                    },
                ),
            )
            return TaskResult.fail(str(exc), retryable=False)

        if normalized_payload != task.payload:
            task = task.model_copy(update={"payload": normalized_payload})

        raw_user_id = task.payload.get("user_id")
        user_id: str | int | None = raw_user_id if isinstance(raw_user_id, (int, str)) else None
        metadata = {
            "source": "queue",
            "queue_name": self.queue_name,
            "task_id": task.id,
            "task_type": task.task_type.value,
            "content_id": task.content_id,
            "retry_count": task.retry_count,
        }

        with (
            bound_log_context(
                task_id=task.id,
                task_type=task.task_type.value,
                queue_name=self.queue_name,
                worker_id=self.worker_id,
                content_id=task.content_id,
                user_id=user_id,
                source="queue",
            ),
            langfuse_trace_context(
                trace_name=f"queue.{task.task_type.value.lower()}",
                user_id=user_id,
                session_id=self.worker_id,
                metadata=metadata,
                tags=["queue", self.queue_name, task.task_type.value.lower()],
            ),
        ):
            try:
                logger.info(
                    "Task processing started",
                    extra=_task_extra(
                        task,
                        processor=self,
                        operation="process_task",
                        status="started",
                        context_data={"retry_count": task.retry_count},
                    ),
                )
                logger.debug(
                    "Task payload loaded",
                    extra=_task_extra(
                        task,
                        processor=self,
                        operation="load_task",
                        context_data={"payload_keys": sorted(task.payload.keys())},
                    ),
                )
                result = self.dispatcher.dispatch(task, self.context)
                if not result.success and not result.error_message and not result.deferred:
                    result = TaskResult(
                        success=False,
                        error_message=f"{task.task_type.value} returned False",
                        retry_delay_seconds=result.retry_delay_seconds,
                        retryable=result.retryable,
                    )
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                logger_method = logger.info if result.success else logger.warning
                logger_method(
                    "Task processing completed",
                    extra=_task_extra(
                        task,
                        processor=self,
                        operation="process_task",
                        status="completed" if result.success else "failed",
                        duration_ms=elapsed_ms,
                        context_data={
                            "result_success": result.success,
                            "retryable": result.retryable,
                            "error_message": result.error_message,
                        },
                    ),
                )
                return result

            except Exception as exc:  # noqa: BLE001
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                logger.exception(
                    "Task processing raised exception",
                    extra=_task_extra(
                        task,
                        processor=self,
                        operation="process_task",
                        status="failed",
                        duration_ms=elapsed_ms,
                        context_data={"failure_class": type(exc).__name__},
                    ),
                )
                return TaskResult.fail(str(exc))

    def _finalize_processed_task(
        self,
        *,
        task: TaskEnvelope,
        result: TaskResult,
    ) -> dict[str, object] | None:
        """Persist task completion/retry state without crashing the worker loop."""
        retry_count = task.retry_count
        max_retries = self.settings.queue.max_retries
        should_retry = task_will_retry(
            result,
            retry_count=retry_count,
            max_retries=max_retries,
        )
        retry_delay_seconds = None
        if should_retry:
            retry_delay_seconds = result.retry_delay_seconds or min(60 * (2**retry_count), 3600)

        if task.lease_token is None or task.locked_by is None:
            logger.error(
                "Task finalization refused because claim ownership is missing",
                extra=_task_extra(
                    task,
                    processor=self,
                    operation="finalize_task",
                    status="failed",
                    context_data={"failure_class": "MissingLeaseOwnership"},
                ),
            )
            return None

        try:
            finalization = self.queue_service.finalize_task(
                task.id,
                worker_id=task.locked_by,
                lease_token=task.lease_token,
                success=False if result.deferred else result.success,
                error_message=None if result.deferred else result.error_message,
                retryable=False if result.deferred else result.retryable,
                current_retry_count=retry_count,
                max_retries=max_retries,
                retry_delay_seconds=(
                    result.retry_delay_seconds if result.deferred else retry_delay_seconds
                ),
                deferred=result.deferred,
            )

            if isinstance(finalization, TaskTransition):
                return finalization.model_dump(mode="python")
            if isinstance(finalization, dict) or finalization is None:
                return finalization
            raise TypeError("Queue finalization returned an unsupported result")
        except OperationalError as exc:
            logger.exception(
                "Task finalization hit a database write error",
                extra=_task_extra(
                    task,
                    processor=self,
                    operation="finalize_task",
                    status="failed",
                    context_data={
                        "failure_class": type(exc).__name__,
                        "retryable": result.retryable,
                        "result_success": result.success,
                    },
                ),
            )
            return None

    def run(self, max_tasks: int | None = None) -> int:
        """
        Run the claim loop until `running` is cleared.

        Shutdown is the caller's job: this loop runs on a worker thread, where
        signal handlers cannot be installed.

        Args:
            max_tasks: Maximum number of tasks to process. None for unlimited.

        Returns:
            The number of tasks processed successfully.
        """
        logger.debug("Entering run method with max_tasks=%s", max_tasks)
        logger.info(
            "Starting sequential task processor (worker_id: %s, queue=%s)",
            self.worker_id,
            self.queue_name,
        )

        processed_count = 0
        consecutive_empty_polls = 0
        max_empty_polls = 5
        startup_polls = 0
        startup_phase_polls = 10

        logger.info(
            "Entering startup phase with %s aggressive polls (100ms intervals)",
            startup_phase_polls,
        )

        logger.debug("About to enter main loop, self.running=%s", self.running)
        while self.running:
            try:
                logger.debug("Attempting to dequeue task (poll #%s)", startup_polls + 1)
                task_data = self.queue_service.dequeue(
                    worker_id=self.worker_id,
                    queue_name=self.queue_name,
                )
                logger.debug("Dequeue result: %s", task_data is not None)

                if not task_data:
                    consecutive_empty_polls += 1
                    startup_polls += 1

                    if startup_polls <= startup_phase_polls:
                        logger.debug(
                            "Startup phase: quick poll %s/%s",
                            startup_polls,
                            startup_phase_polls,
                        )
                        self._idle_wait(0.1)
                    elif consecutive_empty_polls >= max_empty_polls:
                        logger.debug("Queue empty, backing off...")
                        self._idle_wait(5.0)
                    else:
                        self._idle_wait(1.0)
                    continue

                consecutive_empty_polls = 0

                if startup_polls > 0 and startup_polls <= startup_phase_polls:
                    logger.info("Exiting startup phase - found first task")

                try:
                    task = TaskEnvelope.from_queue_data(task_data)
                except ValidationError as exc:
                    task_id = _task_data_value(task_data, "id")
                    logger.error(
                        "Invalid task payload",
                        extra=build_log_extra(
                            component="task_processor",
                            operation="task_parse",
                            event_name="task.invalid_payload",
                            status="failed",
                            item_id=task_id,
                            task_id=task_id,
                            queue_name=self.queue_name,
                            worker_id=self.worker_id,
                            source="queue",
                            context_data={
                                "failure_class": type(exc).__name__,
                                "task_data": _task_data_for_log(task_data),
                            },
                        ),
                    )
                    if task_id is not None:
                        invalid_task = _invalid_task_envelope(
                            task_data,
                            task_id=int(task_id),
                        )
                        self._finalize_processed_task(
                            task=invalid_task,
                            result=TaskResult.fail("Invalid task payload", retryable=False),
                        )
                    continue
                result, finalization = self._process_and_finalize_task(task)

                if finalization is None:
                    logger.warning(
                        "Task result was not persisted",
                        extra=_task_extra(
                            task,
                            processor=self,
                            operation="finalize_task",
                            status="degraded",
                            context_data={
                                "failure_class": "TaskFinalizationRejected",
                                "result_success": result.success,
                            },
                        ),
                    )
                    continue

                if result.success:
                    processed_count += 1
                    logger.info(
                        "Successfully completed task %s (total processed: %s)",
                        task.id,
                        processed_count,
                    )
                else:
                    max_retries = self.settings.queue.max_retries
                    if finalization and finalization.get("status") == "pending":
                        logger.info(
                            (
                                "Task deferred by processor"
                                if result.deferred
                                else "Task retry requested by processor"
                            ),
                            extra=_task_extra(
                                task,
                                processor=self,
                                operation="retry_task",
                                event_name=(
                                    "task.deferred" if result.deferred else "task.retry_scheduled"
                                ),
                                status="deferred" if result.deferred else "retry_scheduled",
                                context_data={
                                    "retry_count": finalization.get("retry_count"),
                                    "max_retries": max_retries,
                                    "delay_seconds": finalization.get("retry_delay_seconds"),
                                },
                            ),
                        )
                    elif not result.retryable:
                        logger.info(
                            "Task failed with non-retryable error",
                            extra=_task_extra(
                                task,
                                processor=self,
                                operation="process_task",
                                status="failed",
                                context_data={
                                    "retryable": False,
                                    "error_message": result.error_message or "unknown error",
                                },
                            ),
                        )
                    else:
                        logger.error(
                            "Task exceeded max retries",
                            extra=_task_extra(
                                task,
                                processor=self,
                                operation="process_task",
                                status="failed",
                                context_data={"max_retries": max_retries},
                            ),
                        )

                if max_tasks and processed_count >= max_tasks:
                    logger.info("Reached max tasks limit (%s), stopping", max_tasks)
                    break

            except OperationalError as exc:
                time.sleep(self._recover_from_operational_error(exc))
            except Exception as exc:  # noqa: BLE001
                logger.error("Error in main loop: %s", exc, exc_info=True)
                time.sleep(5)

        logger.info(
            "Processor shutting down (worker_id: %s, processed %s tasks)",
            self.worker_id,
            processed_count,
        )
        return processed_count

    def run_single_task(
        self,
        task_data: ClaimedTask | Mapping[str, Any],
    ) -> bool:
        """
        Process a single task without the main loop.
        Useful for testing or one-off processing.
        """
        setup_logging()
        logger.info("Processing single task: %s", _task_data_value(task_data, "id") or "unknown")

        try:
            task = TaskEnvelope.from_queue_data(task_data)
        except ValidationError as exc:
            task_id = _task_data_value(task_data, "id")
            log_task_id = task_id if isinstance(task_id, (str, int)) else None
            logger.error(
                "Invalid task payload",
                extra=build_log_extra(
                    component="task_processor",
                    operation="task_parse",
                    event_name="task.invalid_payload",
                    status="failed",
                    item_id=log_task_id,
                    task_id=log_task_id,
                    queue_name=self.queue_name,
                    worker_id=self.worker_id,
                    source="queue",
                    context_data={
                        "failure_class": type(exc).__name__,
                        "task_data": _task_data_for_log(task_data),
                    },
                ),
            )
            if log_task_id is not None:
                invalid_task = _invalid_task_envelope(
                    task_data,
                    task_id=int(log_task_id),
                )
                self._finalize_processed_task(
                    task=invalid_task,
                    result=TaskResult.fail("Invalid task payload", retryable=False),
                )
            return False

        result, finalization = self._process_and_finalize_task(task)

        if finalization and finalization.get("status") == "pending":
            logger.info(
                "Task %s %s",
                task.id,
                "deferred" if result.deferred else "scheduled for retry",
            )

        return bool(
            result.success
            and finalization is not None
            and finalization.get("status") == "completed"
        )
