"""Task handler for link-first news article enrichment."""

from __future__ import annotations

from app.core.logging import get_logger
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.news_article_enrichment import enrich_news_item_article
from app.services.queue import TaskType

logger = get_logger(__name__)


class EnrichNewsItemArticleHandler:
    """Download linked article bodies for short-form news items before summarization."""

    task_type = TaskType.ENRICH_NEWS_ITEM_ARTICLE

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
                result = enrich_news_item_article(db, news_item_id=news_item_id)
                if not result.success and result.error_message == "News item not found":
                    return TaskResult.fail(result.error_message, retryable=False)
                context.queue_service.enqueue(
                    TaskType.PROCESS_NEWS_ITEM,
                    payload={"news_item_id": news_item_id},
                )
            if result.success:
                return TaskResult.ok()
            logger.info(
                "News article enrichment fell back to metadata-only summarization",
                extra={
                    "component": "enrich_news_item_article",
                    "operation": "handle",
                    "item_id": str(news_item_id),
                    "context_data": {"error": result.error_message},
                },
            )
            return TaskResult.ok()
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "News article enrichment raised exception",
                extra={
                    "component": "enrich_news_item_article",
                    "operation": "handle",
                    "item_id": str(news_item_id),
                    "context_data": {"task_id": task.id},
                },
            )
            return TaskResult.fail(str(exc))
