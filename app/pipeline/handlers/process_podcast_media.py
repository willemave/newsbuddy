"""Podcast media processing task handler."""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.db import Content
from app.pipeline.podcast_workers import PodcastMediaWorker
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.queue import TaskType

logger = get_logger(__name__)


def _is_non_retryable_media_error(error_message: str | None) -> bool:
    """Return whether a persisted media failure is terminal without retry."""
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


class ProcessPodcastMediaHandler:
    """Handle podcast media processing in a single hot-path worker lease."""

    task_type = TaskType.PROCESS_PODCAST_MEDIA

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Download, normalize, transcribe, persist, and queue summarize."""
        try:
            content_id = task.content_id or task.payload.get("content_id")
            if not content_id:
                logger.error("No content_id provided for process_podcast_media task")
                return TaskResult.fail("No content_id provided")

            worker = PodcastMediaWorker()
            success = worker.process_media_task(int(content_id))
            if success:
                return TaskResult.ok()

            persisted_error: str | None = None
            with context.db_factory() as db:
                content_row = (
                    db.query(Content.error_message).filter(Content.id == int(content_id)).first()
                )
                if content_row:
                    persisted_error = content_row[0]
            if _is_non_retryable_media_error(persisted_error):
                return TaskResult.fail(persisted_error, retryable=False)
            return TaskResult.fail(persisted_error)
        except Exception as exc:  # noqa: BLE001
            logger.error("Podcast media processing error: %s", exc, exc_info=True)
            return TaskResult.fail(str(exc))
