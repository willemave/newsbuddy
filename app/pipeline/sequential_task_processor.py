"""One queue claim loop: dequeue, process, finalize, idle, repeat.

Process-wide concerns - signal handling, the LISTEN connection, model warmups -
belong to whoever runs these loops (see `threaded_task_processor.py`).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.core.db import dispose_db_engine
from app.core.logging import get_logger, setup_logging
from app.core.observability import bound_log_context, build_log_extra, get_task_event_name
from app.core.settings import get_settings
from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.internal.queue import ClaimedTask, TaskTransition
from app.pipeline.dispatcher import TaskDispatcher
from app.pipeline.handler_registry import build_handlers_for_queue
from app.pipeline.queue_notifications import QueueNotificationListener
from app.pipeline.task_context import TaskContext
from app.pipeline.task_handler import TaskHandler
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.pipeline.task_specs import TASK_SPECS, get_task_spec
from app.services.gateways.task_queue_gateway import TaskQueueGateway
from app.services.queue import QueueService

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
    task: TaskEnvelope | ClaimedTask | None,
    *,
    processor: SequentialTaskProcessor,
    operation: str,
    event_name: str | None = None,
    status: str | None = None,
    duration_ms: float | None = None,
    context_data: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build structured logger metadata for a queue task."""
    task_type = None
    if task is not None:
        task_type = task.task_type.value if isinstance(task.task_type, TaskType) else task.task_type
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


def _lease_heartbeat_interval_seconds(lease_seconds: int) -> float:
    """Choose a bounded cadence that renews well before lease expiry."""
    return min(lease_seconds / 3, 30.0)


def _lease_heartbeat_retry_seconds(remaining_seconds: float) -> float:
    """Retry a failed renewal promptly while staying within the lease window."""
    return min(5.0, max(remaining_seconds, 0.0))


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

    def _lease_seconds(self) -> int:
        """Return the configured lease duration normalized for queue operations."""
        return max(self.settings.queue.worker_timeout_seconds, 1)

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
    def _lease_heartbeat(self, claim: ClaimedTask) -> Iterator[threading.Event]:
        """Renew the lease for the current task while it is being processed."""
        lease_seconds = self._lease_seconds()
        interval_seconds = _lease_heartbeat_interval_seconds(lease_seconds)
        stop_event = threading.Event()
        ownership_lost = threading.Event()

        def _run() -> None:
            last_renewed_at = time.monotonic()
            wait_seconds = interval_seconds
            while not stop_event.wait(wait_seconds):
                try:
                    renewed = self.queue_service.renew_lease(
                        claim,
                        lease_seconds=lease_seconds,
                    )
                except SQLAlchemyError as exc:
                    elapsed = time.monotonic() - last_renewed_at
                    if elapsed >= lease_seconds:
                        ownership_lost.set()
                        logger.exception(
                            "Task lease heartbeat exhausted its renewal window",
                            extra=_task_extra(
                                claim,
                                processor=self,
                                operation="renew_lease",
                                event_name="task.lease_heartbeat_stopped",
                                status="failed",
                                context_data={"failure_class": type(exc).__name__},
                            ),
                        )
                        return
                    logger.warning(
                        "Task lease heartbeat renewal hit a transient database error",
                        exc_info=True,
                        extra=_task_extra(
                            claim,
                            processor=self,
                            operation="renew_lease",
                            event_name="task.lease_heartbeat_retrying",
                            status="degraded",
                            context_data={"failure_class": type(exc).__name__},
                        ),
                    )
                    wait_seconds = _lease_heartbeat_retry_seconds(lease_seconds - elapsed)
                    continue
                except Exception as exc:  # noqa: BLE001
                    ownership_lost.set()
                    logger.exception(
                        "Task lease heartbeat stopped after an unexpected error",
                        extra=_task_extra(
                            claim,
                            processor=self,
                            operation="renew_lease",
                            event_name="task.lease_heartbeat_stopped",
                            status="failed",
                            context_data={"failure_class": type(exc).__name__},
                        ),
                    )
                    return
                if renewed:
                    last_renewed_at = time.monotonic()
                    wait_seconds = interval_seconds
                    continue
                ownership_lost.set()
                logger.warning(
                    "Task lease heartbeat stopped after renewal failure",
                    extra=_task_extra(
                        claim,
                        processor=self,
                        operation="renew_lease",
                        event_name="task.lease_heartbeat_stopped",
                        status="degraded",
                    ),
                )
                return

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        try:
            yield ownership_lost
        finally:
            stop_event.set()
            thread.join(timeout=1.0)

    def _context_for_claim(
        self,
        claim: ClaimedTask,
        ownership_lost: threading.Event,
    ) -> TaskContext:
        """Bind exact lease renewal to the handler context for this claim."""
        lease_seconds = self._lease_seconds()

        def renew_claim() -> bool:
            renewed = self.queue_service.renew_lease(
                claim,
                lease_seconds=lease_seconds,
            )
            if not renewed:
                ownership_lost.set()
            return renewed

        return replace(
            self.context,
            lease_renewer=renew_claim,
        )

    def _process_and_finalize_task(
        self,
        claim: ClaimedTask,
        task: TaskEnvelope,
    ) -> tuple[TaskResult, TaskTransition | None]:
        """Run one task under a lease heartbeat and persist the outcome."""
        with self._lease_heartbeat(claim) as ownership_lost:
            result = self.process_task(
                task,
                context=self._context_for_claim(claim, ownership_lost),
            )
            if ownership_lost.is_set():
                logger.warning(
                    "Task result not finalized because lease ownership was lost",
                    extra=_task_extra(
                        task,
                        processor=self,
                        operation="finalize_task",
                        status="degraded",
                        context_data={"failure_class": "LeaseOwnershipLost"},
                    ),
                )
                return result, None
            finalization = self._finalize_processed_task(claim=claim, result=result)
        return result, finalization

    def process_task(
        self,
        task: TaskEnvelope,
        *,
        context: TaskContext | None = None,
    ) -> TaskResult:
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
        with bound_log_context(
            task_id=task.id,
            task_type=task.task_type.value,
            queue_name=self.queue_name,
            worker_id=self.worker_id,
            content_id=task.content_id,
            user_id=user_id,
            source="queue",
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
                result = self.dispatcher.dispatch(task, context or self.context)
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
        claim: ClaimedTask,
        result: TaskResult,
    ) -> TaskTransition | None:
        """Persist task completion/retry state without crashing the worker loop."""
        try:
            return self.queue_service.finalize_task(
                claim,
                result,
                max_retries=self.settings.queue.max_retries,
            )
        except SQLAlchemyError as exc:
            logger.exception(
                "Task finalization hit a database write error",
                extra=_task_extra(
                    claim,
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
                    task = TaskEnvelope.from_claim(task_data)
                except ValidationError as exc:
                    logger.error(
                        "Invalid task payload",
                        extra=build_log_extra(
                            component="task_processor",
                            operation="task_parse",
                            event_name="task.invalid_payload",
                            status="failed",
                            item_id=task_data.id,
                            task_id=task_data.id,
                            queue_name=self.queue_name,
                            worker_id=self.worker_id,
                            source="queue",
                            context_data={
                                "failure_class": type(exc).__name__,
                                "task_data": task_data.model_dump(mode="json"),
                            },
                        ),
                    )
                    self._finalize_processed_task(
                        claim=task_data,
                        result=TaskResult.fail("Invalid task payload", retryable=False),
                    )
                    continue
                result, finalization = self._process_and_finalize_task(task_data, task)

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
                    if finalization.status is TaskStatus.PENDING:
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
                                    "retry_count": finalization.retry_count,
                                    "max_retries": max_retries,
                                    "delay_seconds": finalization.retry_delay_seconds,
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
        claim: ClaimedTask,
    ) -> bool:
        """
        Process a single task without the main loop.
        Useful for testing or one-off processing.
        """
        setup_logging()
        logger.info("Processing single task: %s", claim.id)

        try:
            task = TaskEnvelope.from_claim(claim)
        except ValidationError as exc:
            logger.error(
                "Invalid task payload",
                extra=build_log_extra(
                    component="task_processor",
                    operation="task_parse",
                    event_name="task.invalid_payload",
                    status="failed",
                    item_id=claim.id,
                    task_id=claim.id,
                    queue_name=self.queue_name,
                    worker_id=self.worker_id,
                    source="queue",
                    context_data={
                        "failure_class": type(exc).__name__,
                        "task_data": claim.model_dump(mode="json"),
                    },
                ),
            )
            self._finalize_processed_task(
                claim=claim,
                result=TaskResult.fail("Invalid task payload", retryable=False),
            )
            return False

        result, finalization = self._process_and_finalize_task(claim, task)

        if finalization is not None and finalization.status is TaskStatus.PENDING:
            logger.info(
                "Task %s %s",
                task.id,
                "deferred" if result.deferred else "scheduled for retry",
            )

        return bool(
            result.success
            and finalization is not None
            and finalization.status is TaskStatus.COMPLETED
        )
