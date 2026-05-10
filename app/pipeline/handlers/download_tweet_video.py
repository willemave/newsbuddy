"""Tweet video audio download task handler."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.logging import get_logger
from app.models.contracts import ContentStatus
from app.models.db import Content
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.audio_pipeline import download_audio_via_ytdlp
from app.services.queue import TaskType

logger = get_logger(__name__)


class DownloadTweetVideoAudioHandler:
    """Download audio from a tweet's embedded video via yt-dlp."""

    task_type = TaskType.DOWNLOAD_TWEET_VIDEO_AUDIO

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        content_id = task.content_id or task.payload.get("content_id")
        if not content_id:
            return TaskResult.fail("No content_id provided", retryable=False)

        content_id = int(content_id)
        try:
            with context.db_factory() as db:
                content = db.query(Content).filter(Content.id == content_id).first()
                if not content:
                    return TaskResult.fail("Content not found", retryable=False)

                metadata = dict(content.content_metadata or {})
                if not context.settings.tweet_video_enabled or not metadata.get("has_video"):
                    metadata["has_video"] = False
                    content.content_metadata = metadata
                    db.commit()
                    context.queue.enqueue(TaskType.SUMMARIZE, content_id=content_id)
                    return TaskResult.ok()

                duration_ms = metadata.get("video_duration_ms")
                if isinstance(duration_ms, int):
                    max_ms = context.settings.tweet_video_max_duration_seconds * 1000
                    if duration_ms > max_ms:
                        metadata["has_video"] = False
                        metadata["tweet_video_skip_reason"] = "duration_limit"
                        content.content_metadata = metadata
                        db.commit()
                        context.queue.enqueue(TaskType.SUMMARIZE, content_id=content_id)
                        return TaskResult.ok()

                target_dir = context.settings.tweet_video_media_dir / f"content-{content_id}"
                tweet_url = (
                    metadata.get("tweet_url") or metadata.get("discussion_url") or str(content.url)
                )
                audio_path = download_audio_via_ytdlp(
                    str(tweet_url),
                    target_dir,
                    output_stem=f"tweet-{content_id}",
                )

                metadata["video_audio_path"] = str(audio_path)
                metadata["tweet_video_downloaded_at"] = datetime.now(UTC).isoformat()
                content.content_metadata = metadata
                content.status = ContentStatus.PROCESSING.value
                db.commit()

            context.queue.enqueue(TaskType.TRANSCRIBE_TWEET_VIDEO, content_id=content_id)
            return TaskResult.ok()

        except Exception as exc:  # noqa: BLE001
            logger.exception("Tweet video audio download failed for content %s", content_id)
            _degrade_to_text_summary(
                task_type="download_tweet_video_audio",
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
