"""Generic LLM task queue handler."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.contracts import LlmTaskKind, LlmTaskStatus, LlmWorkflowState, TaskType
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.learning_deck_generation import LearningDeckGenerationWaiting
from app.services.learning_deck_task_generation import run_learning_deck_task
from app.services.llm_tasks import LlmTaskError, set_llm_task_status
from app.services.share_actions import run_share_action_task

logger = get_logger(__name__)

TERMINAL_LLM_TASK_STATUSES = {
    LlmTaskStatus.COMPLETED.value,
    LlmTaskStatus.FAILED.value,
    LlmTaskStatus.CANCELLED.value,
}


class RunLlmTaskHandler:
    """Run one generic LLM task workflow."""

    task_type = TaskType.RUN_LLM_TASK

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        llm_task_id = task.payload.get("llm_task_id")
        if not llm_task_id:
            return TaskResult.fail("Missing llm_task_id", retryable=False)
        try:
            llm_task_id_int = int(llm_task_id)
        except (TypeError, ValueError):
            return TaskResult.fail(f"Invalid llm_task_id: {llm_task_id!r}", retryable=False)

        with context.db_factory() as db:
            from app.models.db import LlmTask

            llm_task = db.query(LlmTask).filter(LlmTask.id == llm_task_id_int).first()
            if llm_task is None:
                return TaskResult.fail("LLM task not found", retryable=False)
            if llm_task.status in TERMINAL_LLM_TASK_STATUSES:
                return TaskResult.ok()
            try:
                executors = {
                    LlmTaskKind.SHARE_ACTION.value: lambda: run_share_action_task(
                        db,
                        llm_task_id=llm_task_id_int,
                    ),
                    LlmTaskKind.LEARNING_DECK.value: lambda: run_learning_deck_task(
                        db,
                        llm_task_id=llm_task_id_int,
                        ensure_lease=context.renew_current_lease,
                    ),
                }
                executor = executors.get(str(llm_task.task_kind or ""))
                if executor is None:
                    message = f"Unsupported LLM task kind: {llm_task.task_kind}"
                    _mark_llm_task_failed(
                        db,
                        llm_task_id=llm_task_id_int,
                        error_type="unsupported_task_kind",
                        message=message,
                    )
                    return TaskResult.fail(message, retryable=False)
                executor()
            except LearningDeckGenerationWaiting as exc:
                return TaskResult.defer(retry_delay_seconds=exc.retry_delay_seconds)
            except LlmTaskError as exc:
                _mark_llm_task_failed(
                    db,
                    llm_task_id=llm_task_id_int,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                return TaskResult.fail(str(exc), retryable=False)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "RUN_LLM_TASK_ERROR: Failed to run llm_task_id=%s",
                    llm_task_id_int,
                    extra={
                        "component": "llm_task",
                        "operation": "run",
                        "item_id": llm_task_id_int,
                        "context_data": {"error": str(exc)},
                    },
                )
                _mark_llm_task_failed(
                    db,
                    llm_task_id=llm_task_id_int,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                return TaskResult.fail(str(exc), retryable=False)

        return TaskResult.ok()


def _mark_llm_task_failed(
    db: Session,
    *,
    llm_task_id: int,
    error_type: str,
    message: str,
) -> None:
    """Keep the canonical ledger aligned with a terminal queue failure."""
    from app.models.db import LlmTask

    db.rollback()
    task = db.query(LlmTask).filter(LlmTask.id == llm_task_id).first()
    if task is None or task.status in TERMINAL_LLM_TASK_STATUSES:
        return
    set_llm_task_status(
        db,
        task,
        status=LlmTaskStatus.FAILED,
        workflow_state=LlmWorkflowState.FAILED,
        note="LLM task execution failed",
        error_type=error_type[:128],
        error_message=(message or "LLM task execution failed")[:4000],
    )
    db.commit()
