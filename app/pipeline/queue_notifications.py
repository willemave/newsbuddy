"""Process-wide LISTEN/NOTIFY fan-out for queue worker threads."""

from __future__ import annotations

import threading
from types import ModuleType
from typing import Any

from sqlalchemy.engine import make_url

from app.core.logging import get_logger

try:
    import psycopg as _psycopg
except ImportError:  # pragma: no cover
    psycopg: ModuleType | None = None
else:
    psycopg = _psycopg

logger = get_logger(__name__)

QUEUE_NOTIFY_CHANNEL = "processing_tasks"

_RECONNECT_BACKOFF_START_SECONDS = 1.0
_RECONNECT_BACKOFF_MAX_SECONDS = 30.0
_NOTIFY_POLL_SECONDS = 1.0


def psycopg_conninfo(database_url: str) -> str:
    """Return a psycopg-compatible connection string from a SQLAlchemy URL."""
    normalized = str(database_url)
    try:
        url = make_url(normalized)
    except Exception:  # noqa: BLE001
        return normalized
    if not url.drivername.startswith("postgresql"):
        return normalized
    if "+" not in url.drivername:
        return normalized
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


class QueueNotificationListener:
    """Owns one LISTEN connection and wakes every idle worker thread in the process.

    One connection per process rather than one per claim loop: N worker threads
    would otherwise each hold an idle Postgres connection just to hear about new
    work. The listener thread reconnects on its own after database errors, and
    waiters fall back to plain polling whenever no connection is available.
    """

    def __init__(self, database_url: str) -> None:
        self._conninfo = psycopg_conninfo(database_url)
        self._condition = threading.Condition()
        self._generation = 0
        self._connected = False
        self._stopping = threading.Event()
        self._reset_requested = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the listener thread, if notifications are usable at all."""
        if psycopg is None or self._thread is not None or self._stopping.is_set():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="queue-notification-listener",
            daemon=True,
        )
        self._thread.start()

    def wait(self, timeout_seconds: float) -> bool | None:
        """Block until a notification arrives, the timeout elapses, or shutdown.

        Returns None when notifications are unavailable so that callers fall back
        to plain polling for their idle interval.
        """
        if psycopg is None or not self._connected:
            return None
        with self._condition:
            start_generation = self._generation
            return self._condition.wait_for(
                lambda: self._generation != start_generation or self._stopping.is_set(),
                timeout=timeout_seconds,
            )

    def wake(self) -> None:
        """Release every thread currently waiting on a notification."""
        with self._condition:
            self._generation += 1
            self._condition.notify_all()

    def reset(self) -> None:
        """Drop and reopen the listen connection after a database error."""
        self._reset_requested.set()
        self.wake()

    def close(self) -> None:
        """Stop the listener thread and release all waiters."""
        self._stopping.set()
        self.wake()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)

    def _run(self) -> None:
        backoff_seconds = _RECONNECT_BACKOFF_START_SECONDS
        while not self._stopping.is_set():
            self._reset_requested.clear()
            connection = self._connect()
            if connection is None:
                self._set_connected(False)
                if self._stopping.wait(backoff_seconds):
                    return
                backoff_seconds = min(backoff_seconds * 2, _RECONNECT_BACKOFF_MAX_SECONDS)
                continue

            backoff_seconds = _RECONNECT_BACKOFF_START_SECONDS
            self._set_connected(True)
            try:
                self._consume(connection)
            finally:
                self._set_connected(False)
                self._close_connection(connection)

    def _consume(self, connection: Any) -> None:
        while not self._stopping.is_set() and not self._reset_requested.is_set():
            try:
                for _notification in connection.notifies(timeout=_NOTIFY_POLL_SECONDS):
                    self.wake()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Queue notification listener connection failed; reconnecting",
                    exc_info=True,
                )
                return

    def _connect(self) -> Any | None:
        if psycopg is None:
            return None
        try:
            connection = psycopg.connect(self._conninfo, autocommit=True)
            connection.execute(f"LISTEN {QUEUE_NOTIFY_CHANNEL}")
            return connection
        except Exception:  # noqa: BLE001
            logger.warning(
                "Unable to open queue notification listener; polling only",
                exc_info=True,
            )
            return None

    def _close_connection(self, connection: Any) -> None:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            logger.debug("Queue notification listener close failed", exc_info=True)

    def _set_connected(self, connected: bool) -> None:
        self._connected = connected
        if not connected:
            # Release waiters so they drop back to polling instead of sitting on a
            # condition nothing will signal.
            self.wake()
