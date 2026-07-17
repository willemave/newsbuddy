"""Generate-learning-deck task handler."""

from __future__ import annotations

from app.core.logging import get_logger
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.learning_deck_generation import (
    LearningDeckGenerationWaiting,
    generate_learning_deck,
)
from app.services.queue import TaskType

logger = get_logger(__name__)


class GenerateLearningDeckHandler:
    """Build and publish a Learning Deck artifact for one run."""

    task_type = TaskType.GENERATE_LEARNING_DECK

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        payload = task.payload if isinstance(task.payload, dict) else {}
        run_id = payload.get("learning_deck_run_id")
        if not run_id:
            return TaskResult.fail("Missing learning_deck_run_id", retryable=False)

        try:
            run_id_int = int(run_id)
        except (TypeError, ValueError):
            return TaskResult.fail(f"Invalid learning_deck_run_id: {run_id!r}", retryable=False)

        with context.db_factory() as db:
            try:
                generate_learning_deck(db, learning_deck_run_id=run_id_int)
            except LearningDeckGenerationWaiting as exc:
                return TaskResult.defer(
                    retry_delay_seconds=exc.retry_delay_seconds,
                )
            except ValueError as exc:
                return TaskResult.fail(str(exc), retryable=False)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "GENERATE_LEARNING_DECK_ERROR: Failed to generate run_id=%s",
                    run_id_int,
                    extra={
                        "component": "generate_learning_deck",
                        "operation": "generate",
                        "item_id": run_id_int,
                        "context_data": {"error": str(exc)},
                    },
                )
                return TaskResult.fail(str(exc), retryable=False)

        return TaskResult.ok()
