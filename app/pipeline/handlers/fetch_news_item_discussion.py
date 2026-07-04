"""News-item discussion fetch and summarization task handler."""

from __future__ import annotations

from app.core.logging import get_logger
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.news_item_discussions import refresh_news_item_discussion
from app.services.queue import TaskType

logger = get_logger(__name__)


class FetchNewsItemDiscussionHandler:
    """Handle queued HN/Reddit discussion refreshes for short-form news."""

    task_type = TaskType.FETCH_NEWS_ITEM_DISCUSSION

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Fetch raw comments and generate a stored discussion summary."""
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
                result = refresh_news_item_discussion(
                    db,
                    news_item_id=news_item_id,
                    summarizer=context.llm_service,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "News item discussion handler failed",
                extra={
                    "component": "fetch_news_item_discussion",
                    "operation": "handle",
                    "item_id": str(news_item_id),
                    "context_data": {"task_id": task.id},
                },
            )
            return TaskResult.fail(str(exc), retryable=True)

        if result.success:
            return TaskResult.ok()
        if result.status in {"unsupported", "gone"}:
            logger.info(
                "Skipping terminal news item discussion task",
                extra={
                    "component": "fetch_news_item_discussion",
                    "operation": "handle",
                    "item_id": str(news_item_id),
                    "context_data": {"task_id": task.id},
                },
            )
            return TaskResult.ok()

        return TaskResult.fail(
            result.error_message or "News item discussion refresh failed",
            retryable=result.retryable,
        )
