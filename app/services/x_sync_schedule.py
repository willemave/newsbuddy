"""Scheduling policy for X integration syncs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

SYNC_INTERVAL_GRACE_SECONDS = 5


def get_channel_state(sync_metadata: dict[str, Any], channel: str) -> dict[str, Any]:
    """Return one channel's persisted sync state, or an empty state."""
    state = sync_metadata.get(channel)
    return state if isinstance(state, dict) else {}


def should_skip_sync(
    last_synced_at: datetime | None,
    *,
    now: datetime,
    min_interval_minutes: int,
) -> bool:
    """Return whether a sync is still inside its configured cooldown."""
    if last_synced_at is None:
        return False
    elapsed_seconds = (now - last_synced_at).total_seconds()
    return is_within_sync_interval(elapsed_seconds, min_interval_minutes)


def should_skip_channel_sync(
    previous_state: dict[str, Any],
    *,
    now: datetime,
    min_interval_minutes: int,
) -> bool:
    """Return whether a channel's persisted timestamp is still cooling down."""
    return should_skip_sync(
        _parse_last_synced_at(previous_state),
        now=now,
        min_interval_minutes=min_interval_minutes,
    )


def is_within_sync_interval(elapsed_seconds: float, min_interval_minutes: int) -> bool:
    """Allow small scheduler jitter at the configured interval boundary."""
    return elapsed_seconds + SYNC_INTERVAL_GRACE_SECONDS < min_interval_minutes * 60


def _parse_last_synced_at(state: dict[str, Any]) -> datetime | None:
    raw_value = state.get("last_synced_at")
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed
