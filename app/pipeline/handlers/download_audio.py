"""Podcast audio download task handler."""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.schema import Content
from app.pipeline.podcast_workers import PodcastDownloadWorker
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.queue import TaskType

logger = get_logger(__name__)


def _is_non_retryable_download_error(error_message: str | None) -> bool:
    """Return True for terminal download failures that should not be retried."""
    if not error_message:
        return False
    lowered = error_message.lower()
    markers = (
        "sign in to confirm",
        "requires authentication",
        "cookies not found",
        "private video",
        "video unavailable",
    )
    return any(marker in lowered for marker in markers)


class DownloadAudioHandler:
    """Handle podcast audio download tasks."""

    task_type = TaskType.DOWNLOAD_AUDIO

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Download audio files for podcast content."""
        try:
            content_id = task.content_id or task.payload.get("content_id")
            if not content_id:
                logger.error("No content_id provided for download task")
                return TaskResult.fail("No content_id provided")

            worker = PodcastDownloadWorker()
            success = worker.process_download_task(int(content_id))
            if success:
                return TaskResult.ok()

            persisted_error: str | None = None
            with context.db_factory() as db:
                content_row = (
                    db.query(Content.error_message).filter(Content.id == int(content_id)).first()
                )
                if content_row:
                    persisted_error = content_row[0]

            if _is_non_retryable_download_error(persisted_error):
                return TaskResult.fail(persisted_error, retryable=False)

            return TaskResult.fail(persisted_error)
        except Exception as exc:  # noqa: BLE001
            logger.error("Download error: %s", exc, exc_info=True)
            return TaskResult.fail(str(exc))
