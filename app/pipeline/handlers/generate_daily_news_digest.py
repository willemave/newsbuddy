"""Task handler for per-user daily news digest generation."""

from __future__ import annotations

from datetime import date, datetime

from app.core.logging import get_logger
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.daily_news_digest import (
    normalize_timezone,
    upsert_daily_news_digest_for_user_day,
)
from app.services.queue import TaskType

logger = get_logger(__name__)


class GenerateDailyNewsDigestHandler:
    """Handle queued daily digest generation tasks."""

    task_type = TaskType.GENERATE_DAILY_NEWS_DIGEST

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Generate or update one user's digest row for one local date."""
        payload = task.payload if isinstance(task.payload, dict) else {}
        raw_user_id = payload.get("user_id")
        raw_local_date = payload.get("local_date")
        raw_timezone = payload.get("timezone")
        raw_coverage_end_at = payload.get("coverage_end_at")
        force_regenerate = bool(payload.get("force_regenerate", False))
        skip_if_empty = bool(payload.get("skip_if_empty", False))

        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            return TaskResult.fail("Invalid user_id in digest task payload", retryable=False)

        if user_id <= 0:
            return TaskResult.fail("Invalid user_id in digest task payload", retryable=False)

        if not isinstance(raw_local_date, str) or not raw_local_date.strip():
            return TaskResult.fail("Missing local_date in digest task payload", retryable=False)

        try:
            local_date = date.fromisoformat(raw_local_date)
        except ValueError:
            return TaskResult.fail("Invalid local_date in digest task payload", retryable=False)

        coverage_end_at: datetime | None = None
        if raw_coverage_end_at is not None:
            if not isinstance(raw_coverage_end_at, str) or not raw_coverage_end_at.strip():
                return TaskResult.fail(
                    "Invalid coverage_end_at in digest task payload",
                    retryable=False,
                )
            try:
                coverage_end_at = datetime.fromisoformat(raw_coverage_end_at)
            except ValueError:
                return TaskResult.fail(
                    "Invalid coverage_end_at in digest task payload",
                    retryable=False,
                )

        timezone_name = normalize_timezone(raw_timezone if isinstance(raw_timezone, str) else None)

        try:
            with context.db_factory() as db:
                result = upsert_daily_news_digest_for_user_day(
                    db,
                    user_id=user_id,
                    local_date=local_date,
                    timezone_name=timezone_name,
                    force_regenerate=force_regenerate,
                    coverage_end_at=coverage_end_at,
                    skip_if_empty=skip_if_empty,
                    summarizer=context.llm_service,
                )
                if result.skipped:
                    logger.info(
                        "Skipped daily digest generation for user %s (%s, no sources yet)",
                        user_id,
                        local_date.isoformat(),
                    )
                else:
                    logger.info(
                        "Generated daily digest %s for user %s (%s, sources=%s, created=%s)",
                        result.digest_id,
                        user_id,
                        local_date.isoformat(),
                        result.source_count,
                        result.created,
                    )
            return TaskResult.ok()
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Daily digest generation failed for user %s (%s): %s",
                user_id,
                local_date.isoformat(),
                exc,
                extra={
                    "component": "daily_news_digest_handler",
                    "operation": "generate_daily_news_digest",
                    "item_id": user_id,
                    "context_data": {
                            "user_id": user_id,
                            "local_date": local_date.isoformat(),
                            "timezone": timezone_name,
                            "coverage_end_at": raw_coverage_end_at,
                        },
                    },
                )
            return TaskResult.fail(str(exc))
