"""Task handler for short-form news item normalization."""

from __future__ import annotations

from app.core.logging import get_logger
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.news_processing import process_news_item
from app.services.queue import TaskType

logger = get_logger(__name__)


class ProcessNewsItemHandler:
    """Handle queued news item processing tasks."""

    task_type = TaskType.PROCESS_NEWS_ITEM

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        payload = task.payload if isinstance(task.payload, dict) else {}
        raw_news_item_id = payload.get("news_item_id")
        if not isinstance(raw_news_item_id, (str, int)):
            return TaskResult.fail("Invalid news_item_id in task payload", retryable=False)
        try:
            news_item_id = int(raw_news_item_id)
        except (TypeError, ValueError):
            return TaskResult.fail("Invalid news_item_id in task payload", retryable=False)

        try:
            with context.db_factory() as db:
                result = process_news_item(
                    db,
                    news_item_id=news_item_id,
                    summarizer=context.summarizer,
                )
            if result.success:
                return TaskResult.ok()
            return TaskResult.fail(result.error_message, retryable=result.retryable)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "News item processing raised exception",
                extra={
                    "component": "process_news_item",
                    "operation": "handle",
                    "item_id": str(news_item_id),
                    "context_data": {"task_id": task.id},
                },
            )
            return TaskResult.fail(str(exc))
