"""Follow background audio generation and serve the completed cached file."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

from app.core.db import get_session_factory
from app.core.logging import get_logger
from app.services.audio_episodes.creation import get_user_audio_episode
from app.services.audio_episodes.generation import (
    AudioEpisodeNotFoundError,
    audio_episode_file_path,
)
from app.services.audio_episodes.shared import duration_ms

logger = get_logger(__name__)

AUDIO_EPISODE_FILE_CHUNK_SIZE = 1024 * 256
AUDIO_EPISODE_FOLLOW_POLL_SECONDS = 0.25
AUDIO_EPISODE_FOLLOW_TIMEOUT_SECONDS = 180


class AudioEpisodeAlreadyProcessingError(RuntimeError):
    """Raised when an episode is still active after the follower deadline."""


def follow_audio_episode_stream_chunks(*, audio_episode_id: int, user_id: int) -> Iterator[bytes]:
    """Wait for background generation, then yield the completed MP3 file."""

    SessionLocal = get_session_factory()
    started_at = time.perf_counter()
    first_chunk_ms: float | None = None
    chunk_count = 0
    audio_bytes = 0
    episode_kind: str | None = None
    deadline = started_at + AUDIO_EPISODE_FOLLOW_TIMEOUT_SECONDS
    while time.perf_counter() < deadline:
        with SessionLocal() as db:
            episode = get_user_audio_episode(
                db,
                user_id=user_id,
                audio_episode_id=audio_episode_id,
            )
            if episode is None:
                raise AudioEpisodeNotFoundError(f"Audio episode {audio_episode_id} not found")

            episode_kind = str(episode.kind or "")
            path = audio_episode_file_path(episode)
            if episode.status == "completed" and path is not None and path.exists():
                for chunk in _read_audio_episode_file(path):
                    if first_chunk_ms is None:
                        first_chunk_ms = duration_ms(started_at)
                    audio_bytes += len(chunk)
                    chunk_count += 1
                    yield chunk
                logger.info(
                    "Audio episode stream follower completed",
                    extra={
                        "component": "audio_episodes",
                        "operation": "stream_follow",
                        "status": "completed",
                        "duration_ms": duration_ms(started_at),
                        "item_id": audio_episode_id,
                        "user_id": user_id,
                        "context_data": {
                            "kind": episode_kind,
                            "stream_chunk_count": chunk_count,
                            "audio_bytes": audio_bytes,
                            "time_to_first_chunk_ms": first_chunk_ms or 0,
                        },
                    },
                )
                return

            if episode.status == "failed":
                raise RuntimeError(episode.error_message or "Audio episode generation failed")
            if episode.status not in {"pending", "processing"}:
                raise AudioEpisodeAlreadyProcessingError("Audio episode is not actively generating")

        time.sleep(AUDIO_EPISODE_FOLLOW_POLL_SECONDS)

    logger.info(
        "Audio episode stream follower timed out",
        extra={
            "component": "audio_episodes",
            "operation": "stream_follow",
            "status": "timed_out",
            "duration_ms": duration_ms(started_at),
            "item_id": audio_episode_id,
            "user_id": user_id,
            "context_data": {"kind": episode_kind},
        },
    )
    raise AudioEpisodeAlreadyProcessingError("Audio episode is still generating")


def _read_audio_episode_file(path: Path) -> Iterator[bytes]:
    with path.open("rb") as audio_file:
        while chunk := audio_file.read(AUDIO_EPISODE_FILE_CHUNK_SIZE):
            yield chunk


__all__ = [
    "AUDIO_EPISODE_FILE_CHUNK_SIZE",
    "AUDIO_EPISODE_FOLLOW_POLL_SECONDS",
    "AUDIO_EPISODE_FOLLOW_TIMEOUT_SECONDS",
    "AudioEpisodeAlreadyProcessingError",
    "follow_audio_episode_stream_chunks",
]
