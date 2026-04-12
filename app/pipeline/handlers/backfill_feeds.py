"""Feed backfill task handler."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import ValidationError

from app.core.logging import get_logger
from app.models.internal.feed_backfill import FeedBackfillRequest, FeedBatchBackfillRequest
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.feed_backfill import backfill_feed_for_config
from app.services.queue import TaskType

logger = get_logger(__name__)

ONBOARDING_FEED_BACKFILL_MAX_WORKERS = 4


class BackfillFeedsHandler:
    """Run a bounded concurrent backfill across multiple selected feed configs."""

    task_type = TaskType.BACKFILL_FEEDS

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Run feed backfills in parallel for one onboarding request."""
        del context

        try:
            request = FeedBatchBackfillRequest.model_validate(task.payload or {})
        except ValidationError as exc:
            return TaskResult.fail(str(exc), retryable=False)

        unique_config_ids = list(dict.fromkeys(request.config_ids))
        max_workers = min(ONBOARDING_FEED_BACKFILL_MAX_WORKERS, len(unique_config_ids))
        successes = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_config_id = {
                executor.submit(
                    backfill_feed_for_config,
                    FeedBackfillRequest(
                        user_id=request.user_id,
                        config_id=config_id,
                        count=request.count,
                    ),
                ): config_id
                for config_id in unique_config_ids
            }

            for future in as_completed(future_to_config_id):
                config_id = future_to_config_id[future]
                try:
                    result = future.result()
                    successes += 1
                    logger.info(
                        "Completed onboarding feed backfill",
                        extra={
                            "component": "feed_backfill",
                            "operation": "onboarding_batch",
                            "item_id": str(config_id),
                            "context_data": {
                                "user_id": request.user_id,
                                "config_id": config_id,
                                "saved": result.saved,
                                "scraped": result.scraped,
                                "duplicates": result.duplicates,
                                "errors": result.errors,
                            },
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "Onboarding feed backfill failed",
                        extra={
                            "component": "feed_backfill",
                            "operation": "onboarding_batch",
                            "item_id": str(config_id),
                            "context_data": {
                                "user_id": request.user_id,
                                "config_id": config_id,
                                "error": str(exc),
                            },
                        },
                    )

        if successes > 0:
            return TaskResult.ok()
        return TaskResult.fail("All onboarding feed backfills failed")
