"""Generate-insight-report task handler."""

from __future__ import annotations

from app.core.logging import get_logger
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.insight_report import (
    SYNTHESIS_EFFORT,
    SYNTHESIS_MODEL,
    generate_insight_report,
    persist_insight_report,
)
from app.services.queue import TaskType

logger = get_logger(__name__)


class GenerateInsightReportHandler:
    """Build and persist a long-form insight report for one user."""

    task_type = TaskType.GENERATE_INSIGHT_REPORT

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Generate an insight report, persist it, and enqueue cover-image generation."""
        payload = task.payload if isinstance(task.payload, dict) else {}
        user_id = payload.get("user_id")
        synthesis_model = payload.get("synthesis_model") or SYNTHESIS_MODEL
        effort = payload.get("effort") or SYNTHESIS_EFFORT

        if not user_id:
            logger.error(
                "GENERATE_INSIGHT_REPORT_ERROR: Missing user_id in payload",
                extra={
                    "component": "generate_insight_report",
                    "operation": "validate_payload",
                    "context_data": {"payload_keys": sorted(payload.keys())},
                },
            )
            return TaskResult.fail("Missing user_id", retryable=False)

        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            return TaskResult.fail(f"Invalid user_id: {user_id!r}", retryable=False)

        try:
            with context.db_factory() as db:
                report = generate_insight_report(
                    db,
                    user_id=user_id_int,
                    synthesis_model=synthesis_model,
                    effort=effort,
                )
                content = persist_insight_report(
                    db,
                    user_id=user_id_int,
                    report=report,
                    synthesis_model=synthesis_model,
                    effort=effort,
                )
                if content.id is None:
                    return TaskResult.fail(
                        "persist_insight_report returned content without id",
                        retryable=False,
                    )
                content_id = int(content.id)
        except RuntimeError as exc:
            # Raised when the user has no knowledge saves — don't retry, just
            # record the miss. Cron is responsible for only enqueuing eligible
            # users, so this is a degenerate case (race / stale data).
            logger.warning(
                "GENERATE_INSIGHT_REPORT_SKIPPED: %s",
                exc,
                extra={
                    "component": "generate_insight_report",
                    "operation": "generate",
                    "context_data": {"user_id": user_id_int},
                },
            )
            return TaskResult.fail(str(exc), retryable=False)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "GENERATE_INSIGHT_REPORT_ERROR: Failed to generate for user_id=%s",
                user_id_int,
                extra={
                    "component": "generate_insight_report",
                    "operation": "generate",
                    "context_data": {"user_id": user_id_int, "error": str(exc)},
                },
            )
            return TaskResult.fail(str(exc))

        # Enqueue the cover-image generation follow-up. Uses the same content_id
        # so the image handler can resolve and store the image url in metadata.
        try:
            context.queue_service.enqueue(
                TaskType.GENERATE_IMAGE,
                content_id=content_id,
                payload={"source": "insight_report"},
            )
        except Exception as exc:  # noqa: BLE001
            # Image generation is best-effort; we still want the report to
            # survive a transient queue hiccup. Log and proceed.
            logger.exception(
                "GENERATE_INSIGHT_REPORT_WARN: Failed to enqueue cover image for content_id=%s: %s",
                content_id,
                exc,
                extra={
                    "component": "generate_insight_report",
                    "operation": "enqueue_image",
                    "item_id": content_id,
                    "context_data": {"user_id": user_id_int},
                },
            )

        logger.info(
            "Insight report generated user_id=%s content_id=%s",
            user_id_int,
            content_id,
            extra={
                "component": "generate_insight_report",
                "operation": "complete",
                "item_id": content_id,
                "context_data": {
                    "user_id": user_id_int,
                    "synthesis_model": synthesis_model,
                    "effort": effort,
                },
            },
        )
        return TaskResult.ok()
