"""Tweet video transcription task handler."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.core.logging import get_logger
from app.models.contracts import ContentStatus
from app.models.db import Content
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.audio_pipeline import transcribe_audio_file
from app.services.queue import TaskType

logger = get_logger(__name__)


class TranscribeTweetVideoHandler:
    """Transcribe downloaded tweet video audio and continue summarization."""

    task_type = TaskType.TRANSCRIBE_TWEET_VIDEO

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        content_id = task.content_id or task.payload.get("content_id")
        if not content_id:
            return TaskResult.fail("No content_id provided", retryable=False)

        content_id = int(content_id)
        audio_path: Path | None = None
        try:
            with context.db_factory() as db:
                content = db.query(Content).filter(Content.id == content_id).first()
                if not content:
                    return TaskResult.fail("Content not found", retryable=False)

                metadata = dict(content.content_metadata or {})
                raw_audio_path = metadata.get("video_audio_path")
                if not isinstance(raw_audio_path, str) or not raw_audio_path.strip():
                    raise ValueError("Tweet video audio path missing in metadata")
                audio_path = Path(raw_audio_path)
                if not audio_path.exists():
                    raise FileNotFoundError(f"Tweet video audio file not found: {audio_path}")

                transcript = transcribe_audio_file(audio_path)
                metadata["video_transcript"] = transcript
                metadata["video_transcription_date"] = datetime.now(UTC).isoformat()
                metadata["video_transcription_service"] = "whisper_local"
                content.content_metadata = metadata
                content.status = ContentStatus.PROCESSING.value
                db.commit()

            if audio_path is not None:
                audio_path.unlink(missing_ok=True)
            context.queue.enqueue(TaskType.SUMMARIZE, content_id=content_id)
            return TaskResult.ok()

        except Exception as exc:  # noqa: BLE001
            logger.exception("Tweet video transcription failed for content %s", content_id)
            _degrade_to_text_summary(
                task_type="transcribe_tweet_video",
                content_id=content_id,
                error=str(exc),
                context=context,
            )
            return TaskResult.ok()


def _degrade_to_text_summary(
    *,
    task_type: str,
    content_id: int,
    error: str,
    context: TaskContext,
) -> None:
    with context.db_factory() as db:
        content = db.query(Content).filter(Content.id == content_id).first()
        if content:
            metadata = dict(content.content_metadata or {})
            metadata["has_video"] = False
            metadata["tweet_video_error"] = {
                "stage": task_type,
                "message": error[:500],
                "timestamp": datetime.now(UTC).isoformat(),
            }
            content.content_metadata = metadata
            content.status = ContentStatus.PROCESSING.value
            db.commit()
    context.queue.enqueue(TaskType.SUMMARIZE, content_id=content_id)
