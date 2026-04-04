"""Tests for mark-read command timeout behavior."""

from contextlib import contextmanager

from app.application.commands import mark_read as mark_read_command
from app.repositories import read_status_repository


def test_mark_read_uses_short_busy_timeout(
    db_session,
    test_user,
    test_content,
    monkeypatch,
) -> None:
    """It should bound the content lookup on mark-read requests."""
    timeouts: list[int] = []

    @contextmanager
    def _capture_timeout(_db, timeout_ms: int):
        timeouts.append(timeout_ms)
        yield

    monkeypatch.setattr(
        mark_read_command,
        "temporary_sqlite_busy_timeout",
        _capture_timeout,
    )

    result = mark_read_command.mark_read(
        db_session,
        user_id=test_user.id,
        content_id=test_content.id,
    )

    assert result == {"status": "success", "content_id": test_content.id}
    assert timeouts == [read_status_repository.READ_STATUS_BUSY_TIMEOUT_MS]
