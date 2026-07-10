"""Generate-audio-episode task handler."""

from __future__ import annotations

from app.core.logging import get_logger
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult, task_will_retry
from app.services.audio_episodes import (
    AudioEpisodeInputError,
    AudioEpisodeNotFoundError,
    finalize_audio_episode_failure,
    generate_audio_episode,
)
from app.services.llm_errors import is_llm_error_retryable, iter_exception_chain
from app.services.queue import TaskType
from app.services.voice.narration_tts import PermanentNarrationTtsError

logger = get_logger(__name__)


class GenerateAudioEpisodeHandler:
    """Build script and MP3 audio for one on-demand episode."""

    task_type = TaskType.GENERATE_AUDIO_EPISODE

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        payload = task.payload if isinstance(task.payload, dict) else {}
        audio_episode_id = payload.get("audio_episode_id")
        if not audio_episode_id:
            return TaskResult.fail("Missing audio_episode_id", retryable=False)

        try:
            audio_episode_id_int = int(audio_episode_id)
        except (TypeError, ValueError):
            return TaskResult.fail(
                f"Invalid audio_episode_id: {audio_episode_id!r}",
                retryable=False,
            )

        with context.db_factory() as db:
            try:
                generate_audio_episode(db, audio_episode_id=audio_episode_id_int)
            except Exception as exc:  # noqa: BLE001
                result = TaskResult.fail(
                    str(exc),
                    retryable=_is_generation_error_retryable(exc),
                )
                retry_scheduled = task_will_retry(
                    result,
                    retry_count=task.retry_count,
                    max_retries=context.settings.queue.max_retries,
                )
                finalize_audio_episode_failure(
                    db,
                    audio_episode_id=audio_episode_id_int,
                    error=exc,
                    retry_scheduled=retry_scheduled,
                )
                logger.exception(
                    "GENERATE_AUDIO_EPISODE_ERROR: Failed to generate audio_episode_id=%s",
                    audio_episode_id_int,
                    extra={
                        "component": "generate_audio_episode",
                        "operation": "generate",
                        "item_id": audio_episode_id_int,
                        "context_data": {"error": str(exc)},
                    },
                )
                return result

        return TaskResult.ok()


def _is_generation_error_retryable(exc: Exception) -> bool:
    """Separate permanent local input failures from transient provider causes."""

    permanent_errors = (
        AudioEpisodeInputError,
        AudioEpisodeNotFoundError,
        PermanentNarrationTtsError,
    )
    if any(isinstance(error, permanent_errors) for error in iter_exception_chain(exc)):
        return False
    return is_llm_error_retryable(exc)
