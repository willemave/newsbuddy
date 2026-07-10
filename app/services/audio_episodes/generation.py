"""Audio episode script/TTS generation and durable lifecycle transitions."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.db import AudioEpisode
from app.services.audio_episodes.scripting import (
    estimate_duration_seconds,
    prepare_audio_episode_script,
)
from app.services.audio_episodes.shared import duration_ms, required_int, required_str
from app.services.voice.narration_tts import get_content_narration_tts_service

logger = get_logger(__name__)

AUDIO_EPISODE_PROCESSING_STALE_AFTER = timedelta(minutes=15)


class AudioEpisodeNotFoundError(ValueError):
    """Raised when a queued generation target no longer exists."""


def generate_audio_episode(db: Session, *, audio_episode_id: int) -> AudioEpisode:
    """Generate one persisted episode without deciding queue retry disposition."""

    started_at = time.perf_counter()
    episode = db.query(AudioEpisode).filter(AudioEpisode.id == audio_episode_id).first()
    if episode is None:
        raise AudioEpisodeNotFoundError(f"Audio episode {audio_episode_id} not found")
    if episode.status == "completed" and episode.audio_storage_path:
        return episode
    if episode.status == "processing" and not is_audio_episode_processing_stale(episode):
        return episode

    episode_id = required_int(episode.id, "audio episode id")
    user_id = required_int(episode.user_id, "audio episode user_id")
    episode.status = "processing"
    episode.error_message = None
    episode.started_at = datetime.now(UTC).replace(tzinfo=None)
    episode.completed_at = None
    db.flush()

    script_duration_ms = 0.0
    tts_duration_ms = 0.0
    write_duration_ms = 0.0
    audio_bytes_length = 0
    try:
        script_started_at = time.perf_counter()
        script = prepare_audio_episode_script(db, episode)
        script_duration_ms = duration_ms(script_started_at)
        script_text = required_str(episode.script_text, "audio episode script_text")

        tts_started_at = time.perf_counter()
        audio_bytes = get_content_narration_tts_service().synthesize_dialogue_mp3(
            turns=[turn.model_dump(mode="json") for turn in script.turns],
            item_id=episode_id,
            user_id=user_id,
        )
        tts_duration_ms = duration_ms(tts_started_at)
        audio_bytes_length = len(audio_bytes)

        write_started_at = time.perf_counter()
        audio_path = _write_audio_episode_file(episode_id, audio_bytes)
        write_duration_ms = duration_ms(write_started_at)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Audio episode generation failed before disposition",
            extra={
                "component": "audio_episodes",
                "operation": "generate",
                "duration_ms": duration_ms(started_at),
                "item_id": audio_episode_id,
                "user_id": episode.user_id,
                "context_data": {
                    "kind": episode.kind,
                    "error": str(exc),
                    "script_duration_ms": script_duration_ms,
                    "tts_duration_ms": tts_duration_ms,
                    "write_duration_ms": write_duration_ms,
                    "audio_bytes": audio_bytes_length,
                },
            },
        )
        raise

    episode.status = "completed"
    episode.audio_storage_path = str(audio_path)
    episode.audio_content_type = "audio/mpeg"
    episode.duration_seconds = estimate_duration_seconds(script_text)
    episode.error_message = None
    episode.completed_at = datetime.now(UTC).replace(tzinfo=None)
    db.flush()
    logger.info(
        "Audio episode generation completed",
        extra={
            "component": "audio_episodes",
            "operation": "generate",
            "status": "completed",
            "duration_ms": duration_ms(started_at),
            "item_id": audio_episode_id,
            "user_id": user_id,
            "context_data": {
                "kind": episode.kind,
                "script_duration_ms": script_duration_ms,
                "tts_duration_ms": tts_duration_ms,
                "write_duration_ms": write_duration_ms,
                "audio_bytes": audio_bytes_length,
                "turn_count": len(script.turns),
                "duration_seconds": episode.duration_seconds,
            },
        },
    )
    return episode


def finalize_audio_episode_failure(
    db: Session,
    *,
    audio_episode_id: int,
    error: Exception,
    retry_scheduled: bool,
) -> None:
    """Apply the sole pending-or-failed transition after retry disposition is known."""

    episode = db.query(AudioEpisode).filter(AudioEpisode.id == audio_episode_id).first()
    if episode is None:
        return
    episode.status = "pending" if retry_scheduled else "failed"
    episode.error_message = str(error)
    episode.audio_storage_path = None
    if retry_scheduled:
        episode.started_at = None
        episode.completed_at = None
    else:
        episode.completed_at = datetime.now(UTC).replace(tzinfo=None)
    db.flush()


def is_audio_episode_processing_stale(episode: AudioEpisode) -> bool:
    """Return whether an in-flight episode is old enough for a new attempt."""

    if episode.status != "processing":
        return False
    started_at = episode.started_at
    if started_at is None:
        return True
    processing_age = datetime.now(UTC).replace(tzinfo=None) - started_at
    return processing_age > AUDIO_EPISODE_PROCESSING_STALE_AFTER


def audio_episode_file_path(episode: AudioEpisode) -> Path | None:
    """Return the local MP3 path for a generated episode."""

    storage_path = str(episode.audio_storage_path or "").strip()
    return Path(storage_path) if storage_path else None


def audio_episode_final_file_path(audio_episode_id: int) -> Path:
    settings = get_settings()
    return settings.media_base_dir / "audio_episodes" / f"audio-episode-{audio_episode_id}.mp3"


def _write_audio_episode_file(audio_episode_id: int, audio_bytes: bytes) -> Path:
    path = audio_episode_final_file_path(audio_episode_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio_bytes)
    return path


__all__ = [
    "AUDIO_EPISODE_PROCESSING_STALE_AFTER",
    "AudioEpisodeNotFoundError",
    "audio_episode_file_path",
    "audio_episode_final_file_path",
    "finalize_audio_episode_failure",
    "generate_audio_episode",
    "is_audio_episode_processing_stale",
]
