"""Threaded task processor: several claim loops inside one per-queue process."""

from __future__ import annotations

import signal
import sys
import threading

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.core.settings import get_settings
from app.pipeline.queue_notifications import QueueNotificationListener
from app.pipeline.sequential_task_processor import SequentialTaskProcessor
from app.services.agent_vm_sessions import close_process_agent_vm_sessions
from app.services.news_embeddings import warm_news_embedding_model
from app.services.news_reranker import warm_news_reranker_model
from app.services.queue import QueueService, TaskQueue

logger = get_logger(__name__)

_THREAD_JOIN_POLL_SECONDS = 0.5


def warm_queue_models(queue_name: str) -> None:
    """Load the models a queue's handlers need, once per worker process."""
    if queue_name != TaskQueue.CONTENT.value:
        return
    settings = get_settings()
    if settings.news_list_warm_embeddings:
        try:
            warm_news_embedding_model()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to warm news embedding model")
    if settings.news_list_reranker_enabled:
        try:
            warm_news_reranker_model()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to warm news reranker model")


class ThreadedTaskProcessor:
    """Run N claim loops for one queue inside a single worker process.

    Owns everything that is process-wide rather than per-claim-loop: signal
    handlers, the LISTEN connection, and model warmups. The claim loops
    themselves stay ignorant of all three.

    The queue is already safe for concurrent claims (FOR UPDATE SKIP LOCKED plus
    per-task leases), and the workload is dominated by network I/O and
    subprocesses, so threads buy throughput without a second process paying the
    import and model memory cost again.
    """

    def __init__(
        self,
        queue_name: TaskQueue | str = TaskQueue.CONTENT,
        worker_slot: int = 1,
        *,
        threads: int = 1,
    ) -> None:
        if threads < 1:
            raise ValueError("threads must be at least 1")
        self.queue_name = QueueService._normalize_queue_name(queue_name) or TaskQueue.CONTENT.value
        self.worker_slot = worker_slot
        self.threads = threads
        self.processors: list[SequentialTaskProcessor] = []
        self._listener = QueueNotificationListener(str(get_settings().database_url))
        self._shutdown_requested = False
        self._worker_failure: BaseException | None = None

    def run(self, max_tasks: int | None = None) -> None:
        """Run every claim loop until shutdown.

        Args:
            max_tasks: Per-thread cap on successfully processed tasks, or None
                for unlimited.

        Raises:
            BaseException: The first error to escape a claim loop, re-raised
                after the remaining loops stop, so the supervisor restarts a
                process whose threads died instead of leaving it short-handed.
        """
        if max_tasks is not None and max_tasks < 1:
            raise ValueError("max_tasks must be at least 1")

        # A bounded run preserves the historical process-wide contract by using
        # one claim loop. This keeps the cap exact without a second shared state
        # machine for reserving and returning task-budget permits.
        claim_loop_count = 1 if max_tasks is not None else self.threads

        self._install_signal_handlers()
        warm_queue_models(self.queue_name)
        self._listener.start()
        self.processors = [
            SequentialTaskProcessor(
                self.queue_name,
                self.worker_slot,
                # A lone claim loop keeps the historical unsuffixed worker id, so
                # --threads 1 stays a clean rollback.
                thread_index=index if claim_loop_count > 1 else None,
                notification_listener=self._listener,
            )
            for index in range(1, claim_loop_count + 1)
        ]

        logger.info(
            "Starting threaded task processor (queue=%s, slot=%s, threads=%s)",
            self.queue_name,
            self.worker_slot,
            claim_loop_count,
            extra=self._log_extra(operation="start", status="started"),
        )

        processed_by_worker: dict[str, int] = {}
        worker_threads = [
            threading.Thread(
                target=self._run_processor,
                args=(processor, max_tasks, processed_by_worker),
                name=processor.worker_id,
            )
            for processor in self.processors
        ]
        for worker_thread in worker_threads:
            worker_thread.start()

        try:
            self._join_all(worker_threads)
        finally:
            self._request_shutdown()
            self._join_all(worker_threads)
            self._listener.close()
            close_process_agent_vm_sessions()

        logger.info(
            "Threaded task processor stopped (queue=%s, processed %s tasks)",
            self.queue_name,
            sum(processed_by_worker.values()),
            extra=self._log_extra(
                operation="stop",
                status="completed",
                context_data={"processed_by_worker": dict(processed_by_worker)},
            ),
        )

        if self._worker_failure is not None:
            raise self._worker_failure

    def _run_processor(
        self,
        processor: SequentialTaskProcessor,
        max_tasks: int | None,
        processed_by_worker: dict[str, int],
    ) -> None:
        try:
            processed_by_worker[processor.worker_id] = processor.run(max_tasks=max_tasks)
        except BaseException as exc:
            processed_by_worker[processor.worker_id] = 0
            logger.exception(
                "Worker thread exited with an unhandled error",
                extra=self._log_extra(
                    operation="worker_thread",
                    status="failed",
                    context_data={"worker_id": processor.worker_id},
                ),
            )
            # A dead claim loop is unrecoverable in-process: stop the rest so the
            # supervisor restarts a whole worker instead of a degraded one.
            if self._worker_failure is None:
                self._worker_failure = exc
            self._request_shutdown()

    def _join_all(self, worker_threads: list[threading.Thread]) -> None:
        """Join every thread while leaving the main thread free to take signals."""
        for worker_thread in worker_threads:
            while worker_thread.is_alive():
                worker_thread.join(timeout=_THREAD_JOIN_POLL_SECONDS)

    def _request_shutdown(self) -> None:
        for processor in self.processors:
            processor.running = False
        # Release threads parked on the shared listener so they see the stop flag.
        self._listener.wake()

    def _install_signal_handlers(self) -> None:
        def signal_handler(_signum, _frame):
            if not self._shutdown_requested:
                logger.info(
                    "\n🛑 Received shutdown signal - stopping %s worker thread(s)...",
                    self.threads,
                )
                self._shutdown_requested = True
                self._request_shutdown()
            else:
                logger.warning("\n⚠️  Force shutdown requested - exiting immediately")
                sys.exit(1)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def _log_extra(
        self,
        *,
        operation: str,
        status: str | None = None,
        context_data: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return build_log_extra(
            component="threaded_task_processor",
            operation=operation,
            event_name="task.worker_pool",
            status=status,
            queue_name=self.queue_name,
            source="queue",
            context_data={"configured_threads": self.threads, **(context_data or {})},
        )
