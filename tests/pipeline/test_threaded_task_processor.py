"""Tests for the threaded per-queue task processor."""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.db import ProcessingTask
from app.pipeline.threaded_task_processor import ThreadedTaskProcessor
from app.services.queue import QueueService, TaskQueue, TaskStatus, TaskType


class _FakeProcessor:
    """Stand-in for a claim loop that records how it was constructed and run."""

    instances: list[_FakeProcessor] = []

    def __init__(self, queue_name, worker_slot, **kwargs):
        self.queue_name = queue_name
        self.worker_slot = worker_slot
        self.thread_index = kwargs.get("thread_index")
        self.notification_listener = kwargs.get("notification_listener")
        self.warm_models = kwargs.get("warm_models")
        self.worker_id = f"{queue_name}-processor-{worker_slot}-t{self.thread_index}"
        self.running = True
        self.install_signal_handlers = None
        self.thread_name: str | None = None
        _FakeProcessor.instances.append(self)

    def run(self, max_tasks=None, *, install_signal_handlers=True):
        self.install_signal_handlers = install_signal_handlers
        self.thread_name = threading.current_thread().name
        return 1


@pytest.fixture(autouse=True)
def _reset_fake_processors():
    _FakeProcessor.instances = []
    yield
    _FakeProcessor.instances = []


def _run_with_fake_processors(threads: int) -> ThreadedTaskProcessor:
    processor = ThreadedTaskProcessor(TaskQueue.CONTENT, worker_slot=1, threads=threads)
    with patch(
        "app.pipeline.threaded_task_processor.SequentialTaskProcessor",
        _FakeProcessor,
    ):
        processor.run()
    return processor


def test_single_thread_runs_the_plain_sequential_loop() -> None:
    """--threads 1 must be an exact rollback: no thread index, signals installed."""
    _run_with_fake_processors(threads=1)

    assert len(_FakeProcessor.instances) == 1
    only = _FakeProcessor.instances[0]
    assert only.thread_index is None
    assert only.install_signal_handlers is True
    assert only.thread_name == threading.current_thread().name


def test_each_thread_claims_under_its_own_worker_id() -> None:
    """Threads share a process but must not share a claim identity."""
    _run_with_fake_processors(threads=3)

    assert [instance.thread_index for instance in _FakeProcessor.instances] == [1, 2, 3]
    assert len({instance.worker_id for instance in _FakeProcessor.instances}) == 3


def test_worker_threads_do_not_install_signal_handlers() -> None:
    """Only the main thread may install handlers; a worker thread raises if it tries."""
    _run_with_fake_processors(threads=2)

    assert all(instance.install_signal_handlers is False for instance in _FakeProcessor.instances)
    assert all(
        instance.thread_name != threading.current_thread().name
        for instance in _FakeProcessor.instances
    )


def test_threads_share_one_notification_listener() -> None:
    """N threads must not open N idle LISTEN connections."""
    _run_with_fake_processors(threads=4)

    listeners = {id(instance.notification_listener) for instance in _FakeProcessor.instances}
    assert len(listeners) == 1
    assert _FakeProcessor.instances[0].notification_listener is not None


def test_models_are_warmed_once_per_process() -> None:
    """Model warmups are process-wide, so only one thread should do them."""
    _run_with_fake_processors(threads=3)

    assert [instance.warm_models for instance in _FakeProcessor.instances] == [True, False, False]


def test_shutdown_stops_every_claim_loop() -> None:
    """A shutdown request has to reach all loops, not just the one that saw it."""
    processor = ThreadedTaskProcessor(TaskQueue.CONTENT, threads=3)
    with patch(
        "app.pipeline.threaded_task_processor.SequentialTaskProcessor",
        _FakeProcessor,
    ):
        processor.run()

    assert all(not instance.running for instance in _FakeProcessor.instances)


def test_concurrent_claims_never_hand_out_a_task_twice(
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
