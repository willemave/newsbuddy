"""Onboarding discovery enrichment task handler."""

from __future__ import annotations

from app.core.logging import get_logger
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.onboarding import run_audio_discovery, run_discover_enrich
from app.services.queue import TaskType
from app.services.weekly_discovery_chat import ensure_weekly_discovery_session

logger = get_logger(__name__)


class OnboardingDiscoverHandler:
    """Handle onboarding discovery enrichment tasks."""

    task_type = TaskType.ONBOARDING_DISCOVER

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Run onboarding discovery enrichment for a user."""
        payload = task.payload or {}
        user_id = payload.get("user_id")
        profile_summary = payload.get("profile_summary")
        inferred_topics = payload.get("inferred_topics")
        run_id = payload.get("run_id")

        if not isinstance(user_id, int):
            logger.error(
                "Missing user_id in onboarding discover task",
                extra={
                    "component": "onboarding",
                    "operation": "task_payload",
                    "context_data": {"payload": payload},
                },
            )
            return TaskResult.fail("Missing user_id", retryable=False)

        try:
            with context.db_factory() as db:
                if isinstance(run_id, int):
                    result = run_audio_discovery(db, run_id, user_id=user_id)
                    if not result.success:
                        return TaskResult.fail(
                            result.error_message or "Onboarding audio discovery failed",
                            retryable=False,
                        )
                else:
                    result = run_discover_enrich(
                        db,
                        user_id=user_id,
                        profile_summary=str(profile_summary or ""),
                        inferred_topics=(
                            inferred_topics if isinstance(inferred_topics, list) else []
                        ),
                    )
                    if not result.success:
                        return TaskResult.fail(
                            result.error_message or "Onboarding discovery enrichment failed",
                            retryable=False,
                        )
                ensure_weekly_discovery_session(db, user_id=user_id)
            return TaskResult.ok()
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Onboarding discover task failed",
                extra={
                    "component": "onboarding",
                    "operation": "task_run",
                    "item_id": str(user_id),
                    "context_data": {"error": str(exc)},
                },
            )
            return TaskResult.fail(str(exc))
