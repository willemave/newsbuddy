"""Feed discovery task handler."""

from __future__ import annotations

from app.core.logging import get_logger
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.feed_discovery import run_feed_discovery
from app.services.queue import TaskType
from app.services.weekly_discovery_chat import ensure_weekly_discovery_session

logger = get_logger(__name__)


class DiscoverFeedsHandler:
    """Handle feed discovery tasks."""

    task_type = TaskType.DISCOVER_FEEDS

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Run feed/podcast/YouTube discovery for a user."""
        payload = task.payload or {}
        user_id = payload.get("user_id")
        if not isinstance(user_id, int):
            logger.error(
                "Missing user_id in discover_feeds task",
                extra={
                    "component": "feed_discovery",
                    "operation": "task_payload",
                    "context_data": {"payload": payload},
                },
            )
            return TaskResult.fail("Missing user_id")

        try:
            run_feed_discovery(user_id=user_id, trigger=payload.get("trigger", "cron"))
            with context.db_factory() as db:
                ensure_weekly_discovery_session(db, user_id=user_id)
            return TaskResult.ok()
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Feed discovery task failed",
                extra={
                    "component": "feed_discovery",
                    "operation": "task_run",
                    "item_id": str(user_id),
                    "context_data": {"error": str(exc)},
                },
            )
            return TaskResult.fail(str(exc))
