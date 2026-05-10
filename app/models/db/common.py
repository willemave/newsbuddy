from __future__ import annotations

from datetime import UTC, datetime


def _utcnow() -> datetime:
    """Return a timezone-naive UTC timestamp for DB defaults."""
    return datetime.now(UTC).replace(tzinfo=None)
