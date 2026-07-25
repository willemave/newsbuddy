"""Tests for the process-wide queue notification listener."""

from __future__ import annotations

import threading
from unittest.mock import Mock, patch

from app.pipeline.queue_notifications import QueueNotificationListener, psycopg_conninfo


def test_psycopg_conninfo_strips_sqlalchemy_driver_suffix() -> None:
    conninfo = psycopg_conninfo(
        "postgresql+psycopg://newsly:secret@127.0.0.1:5432/newsly?sslmode=prefer"
    )

    assert conninfo == "postgresql://newsly:secret@127.0.0.1:5432/newsly?sslmode=prefer"


def test_connect_listens_on_the_queue_channel() -> None:
    listener = QueueNotificationListener("postgresql+psycopg://postgres@localhost/newsly")
    connection = Mock()

    with patch("app.pipeline.queue_notifications.psycopg") as mock_psycopg:
        mock_psycopg.connect.return_value = connection

        assert listener._connect() is connection

    mock_psycopg.connect.assert_called_once_with(
        "postgresql://postgres@localhost/newsly",
        autocommit=True,
    )
    connection.execute.assert_called_once_with("LISTEN processing_tasks")


def test_wait_falls_back_to_polling_without_a_connection() -> None:
    """No listen connection means callers should sleep instead of waiting."""
    listener = QueueNotificationListener("postgresql://postgres@localhost/newsly")

    assert listener.wait(0.01) is None


def test_wake_releases_every_waiting_thread() -> None:
    """One NOTIFY has to wake all idle claim loops, not just one."""
    listener = QueueNotificationListener("postgresql://postgres@localhost/newsly")
    listener._connected = True
    results: list[bool | None] = []
    ready = threading.Barrier(4)

    def waiter() -> None:
        ready.wait(timeout=5)
        results.append(listener.wait(5.0))

    threads = [threading.Thread(target=waiter) for _ in range(3)]
    for thread in threads:
        thread.start()

    ready.wait(timeout=5)
    # Give the waiters a moment to park on the condition before signalling.
    threading.Event().wait(0.1)
    listener.wake()

    for thread in threads:
        thread.join(timeout=5)

    assert results == [True, True, True]


def test_close_releases_waiters() -> None:
    """Shutdown must not leave claim loops parked on the condition."""
    listener = QueueNotificationListener("postgresql://postgres@localhost/newsly")
    listener._connected = True
    result: list[bool | None] = []

    def waiter() -> None:
        result.append(listener.wait(5.0))

    thread = threading.Thread(target=waiter)
    thread.start()
    threading.Event().wait(0.1)
    listener.close()
    thread.join(timeout=5)

    assert result == [True]
