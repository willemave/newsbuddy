"""Tests for the threaded per-queue task processor."""

from __future__ import annotations

import queue
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.db import ProcessingTask
from app.pipeline.threaded_task_processor import ThreadedTaskProcessor, warm_queue_models
from app.services.queue import QueueService, TaskQueue, TaskStatus, TaskType


def _fake_processor_class(instances: list, run_error: Exception | None = None):
    """Build a claim-loop stand-in that records how it was constructed and run."""

    class _FakeProcessor:
        def __init__(self, queue_name, worker_slot, **kwargs):
            self.queue_name = queue_name
            self.worker_slot = worker_slot
            self.thread_index = kwargs.get("thread_index")
            self.notification_listener = kwargs.get("notification_listener")
            suffix = "" if self.thread_index is None else f"-t{self.thread_index}"
            self.worker_id = f"{queue_name}-processor-{worker_slot}{suffix}"
            self.running = True
            self.thread_name: str | None = None
            self.max_tasks: int | None = None
            instances.append(self)

        def run(self, max_tasks=None):
            self.thread_name = threading.current_thread().name
            self.max_tasks = max_tasks
            if run_error is not None:
                raise run_error
            return 1

    return _FakeProcessor


def _run_with_fake_processors(
    threads: int,
    run_error: Exception | None = None,
    *,
    max_tasks: int | None = None,
) -> list:
    instances: list = []
    processor = ThreadedTaskProcessor(TaskQueue.CONTENT, worker_slot=1, threads=threads)
    with (
        patch(
            "app.pipeline.threaded_task_processor.SequentialTaskProcessor",
            _fake_processor_class(instances, run_error),
        ),
        patch("app.pipeline.threaded_task_processor.warm_queue_models"),
    ):
        processor.run(max_tasks=max_tasks)
    return instances


def test_a_lone_claim_loop_keeps_the_historical_worker_id() -> None:
    """--threads 1 stays a clean rollback: no thread suffix in the worker id."""
    instances = _run_with_fake_processors(threads=1)

    assert len(instances) == 1
    assert instances[0].thread_index is None


def test_each_thread_claims_under_its_own_worker_id() -> None:
    """Threads share a process but must not share a claim identity."""
    instances = _run_with_fake_processors(threads=3)

    assert [instance.thread_index for instance in instances] == [1, 2, 3]
    assert len({instance.worker_id for instance in instances}) == 3


def test_claim_loops_run_off_the_main_thread() -> None:
    """Signal handlers live on the main thread; the loops must not block it."""
    instances = _run_with_fake_processors(threads=2)

    assert all(instance.thread_name != threading.current_thread().name for instance in instances)


def test_signal_handlers_are_installed_by_the_owning_process() -> None:
    """Claim loops cannot install handlers off the main thread; the pool must."""
    processor = ThreadedTaskProcessor(TaskQueue.CONTENT, threads=2)
    with (
        patch(
            "app.pipeline.threaded_task_processor.SequentialTaskProcessor",
            _fake_processor_class([]),
        ),
        patch("app.pipeline.threaded_task_processor.warm_queue_models"),
        patch("signal.signal") as mock_signal,
    ):
        processor.run()

    assert mock_signal.call_count >= 2


def test_threads_share_one_notification_listener() -> None:
    """N threads must not open N idle LISTEN connections."""
    instances = _run_with_fake_processors(threads=4)

    listeners = {id(instance.notification_listener) for instance in instances}
    assert len(listeners) == 1
    assert instances[0].notification_listener is not None


def test_models_are_warmed_once_per_process() -> None:
    """Warmups are a process concern, not something each claim loop repeats."""
    processor = ThreadedTaskProcessor(TaskQueue.CONTENT, threads=3)
    with (
        patch(
            "app.pipeline.threaded_task_processor.SequentialTaskProcessor",
            _fake_processor_class([]),
        ),
        patch("app.pipeline.threaded_task_processor.warm_queue_models") as warm,
    ):
        processor.run()

    warm.assert_called_once()


def test_shutdown_stops_every_claim_loop() -> None:
    """A shutdown request has to reach all loops, not just the one that saw it."""
    instances = _run_with_fake_processors(threads=3)

    assert all(not instance.running for instance in instances)


def test_max_tasks_is_one_exact_process_wide_budget() -> None:
    """Bounded drains must not multiply the limit or strand idle claim loops."""
    instances = _run_with_fake_processors(threads=6, max_tasks=3)

    assert len(instances) == 1
    assert instances[0].max_tasks == 3
    assert instances[0].thread_index is None


def test_invalid_thread_count_is_rejected_at_the_processor_boundary() -> None:
    with pytest.raises(ValueError, match="threads must be at least 1"):
        ThreadedTaskProcessor(TaskQueue.CONTENT, threads=0)


def test_threaded_processor_drains_each_task_once() -> None:
    """The owning processor must start, join, and stop a real pool of claim loops."""
    pending: queue.Queue[int] = queue.Queue()
    for task_id in range(40):
        pending.put(task_id)
    processed: list[int] = []
    processed_lock = threading.Lock()

    class DrainingProcessor:
        def __init__(self, queue_name, worker_slot, **kwargs):
            thread_index = kwargs.get("thread_index")
            self.worker_id = f"{queue_name}-processor-{worker_slot}-t{thread_index}"
            self.running = True

        def run(self, max_tasks=None):
            count = 0
            while self.running:
                try:
                    task_id = pending.get_nowait()
                except queue.Empty:
                    return count
                with processed_lock:
                    processed.append(task_id)
                count += 1
            return count

    processor = ThreadedTaskProcessor(TaskQueue.CONTENT, threads=6)
    with (
        patch(
            "app.pipeline.threaded_task_processor.SequentialTaskProcessor",
            DrainingProcessor,
        ),
        patch("app.pipeline.threaded_task_processor.warm_queue_models"),
    ):
        processor.run()

    assert sorted(processed) == list(range(40))
    assert pending.empty()


def test_a_crashed_claim_loop_takes_the_process_down() -> None:
    """A dead loop cannot be restarted in-process; the supervisor must recycle it."""
    failure = RuntimeError("claim loop died")

    with pytest.raises(RuntimeError, match="claim loop died"):
        _run_with_fake_processors(threads=3, run_error=failure)


def test_warming_loads_the_reranker_only_when_it_is_enabled() -> None:
    """Content workers eagerly load the reranker only when it is turned on."""
    settings = SimpleNamespace(
        news_list_warm_embeddings=False,
        news_list_reranker_enabled=True,
    )

    with (
        patch("app.pipeline.threaded_task_processor.get_settings", return_value=settings),
        patch("app.pipeline.threaded_task_processor.warm_news_embedding_model") as warm_embeddings,
        patch("app.pipeline.threaded_task_processor.warm_news_reranker_model") as warm_reranker,
    ):
        warm_queue_models(TaskQueue.CONTENT.value)

    warm_embeddings.assert_not_called()
    warm_reranker.assert_called_once_with()


def test_warming_is_skipped_for_non_content_queues() -> None:
    """Only content workers need the news ranking models."""
    settings = SimpleNamespace(
        news_list_warm_embeddings=True,
        news_list_reranker_enabled=True,
    )

    with (
        patch("app.pipeline.threaded_task_processor.get_settings", return_value=settings),
        patch("app.pipeline.threaded_task_processor.warm_news_embedding_model") as warm_embeddings,
        patch("app.pipeline.threaded_task_processor.warm_news_reranker_model") as warm_reranker,
    ):
        warm_queue_models(TaskQueue.MEDIA.value)

    warm_embeddings.assert_not_called()
    warm_reranker.assert_not_called()


def test_queue_service_concurrent_claims_never_hand_out_a_task_twice(
    postgres_harness,
    db_session_factory: sessionmaker,
) -> None:
    """Several claim threads draining one queue must each get distinct tasks."""
    task_count = 40
    thread_count = 6

    session = db_session_factory()
    try:
        session.add_all(
            ProcessingTask(
                task_type=TaskType.SUMMARIZE.value,
                status=TaskStatus.PENDING.value,
                payload={},
                queue_name=TaskQueue.CONTENT.value,
            )
            for _ in range(task_count)
        )
        session.commit()
    finally:
        session.close()

    claimed_ids: list[int] = []
    claims_lock = threading.Lock()
    start = threading.Barrier(thread_count)

    def drain(thread_index: int) -> None:
        # One QueueService per thread: its retry-bucket cursor and cache are
        # plain dicts that must not be shared across claim loops.
        queue_service = QueueService()
        start.wait(timeout=10)
        while True:
            task_data = queue_service.dequeue(
                worker_id=f"content-processor-1-t{thread_index}",
                queue_name=TaskQueue.CONTENT.value,
            )
            if not task_data:
                return
            with claims_lock:
                claimed_ids.append(int(task_data["id"]))

    threads = [
        threading.Thread(target=drain, args=(index,)) for index in range(1, thread_count + 1)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert len(claimed_ids) == task_count
    assert len(set(claimed_ids)) == task_count
