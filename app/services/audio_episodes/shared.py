"""Small shared primitives for the audio episode package."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from app.services.audio_episode_errors import AudioEpisodeInputError

PROMPT_VERSION = 5
PUBLIC_AUDIO_EPISODE_ERROR_MESSAGE = "Couldn't prepare audio. Please try again."


def duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def required_int(value: int | None, field_name: str) -> int:
    if value is None:
        raise AudioEpisodeInputError(f"Missing {field_name}")
    return int(value)


def required_str(value: str | None, field_name: str) -> str:
    if value is None:
        raise AudioEpisodeInputError(f"Missing {field_name}")
    return value


def required_datetime(value: datetime | None, field_name: str) -> datetime:
    if value is None:
        raise AudioEpisodeInputError(f"Missing {field_name}")
    return value


def int_list_from_snapshot_values(values: list[Any]) -> list[int]:
    parsed_values: list[int] = []
    for value in values:
        parsed_value = int_from_snapshot_value(value)
        if parsed_value is not None:
            parsed_values.append(parsed_value)
    return parsed_values


def int_from_snapshot_value(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
