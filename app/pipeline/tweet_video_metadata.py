"""Helpers for tweet video metadata used by content and media workers."""

from __future__ import annotations

from typing import Any


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def resolve_tweet_video_metadata(metadata: dict[str, Any]) -> tuple[bool, int | None]:
    """Return whether metadata describes a native tweet video and its duration."""
    top_level_has_video = _coerce_bool(metadata.get("has_video"))
    top_level_duration_ms = _coerce_int(metadata.get("video_duration_ms"))

    raw_snapshot = metadata.get("tweet_snapshot")
    snapshot = raw_snapshot if isinstance(raw_snapshot, dict) else {}
    snapshot_has_video = _coerce_bool(snapshot.get("has_video"))
    snapshot_duration_ms = _coerce_int(snapshot.get("video_duration_ms"))

    duration_ms = (
        top_level_duration_ms if top_level_duration_ms is not None else snapshot_duration_ms
    )
    return top_level_has_video or snapshot_has_video, duration_ms


def promote_tweet_video_metadata(metadata: dict[str, Any], *, duration_ms: int | None) -> None:
    """Promote recoverable X snapshot video metadata to the active content fields."""
    metadata["has_video"] = True
    if duration_ms is not None:
        metadata["video_duration_ms"] = duration_ms
    metadata.pop("tweet_video_skip_reason", None)
