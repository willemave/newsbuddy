"""Generic LLM task queue handler."""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.contracts import LlmTaskKind
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.llm_tasks import LlmTaskError
from app.services.queue import TaskType
from app.services.share_actions import run_share_action_task

logger = get_logger(__name__)


class RunLlmTaskHandler:
    """Run one generic LLM task workflow."""

    task_type = TaskType.RUN_LLM_TASK

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        payload = task.payload if isinstance(task.payload, dict) else {}
        llm_task_id = payload.get("llm_task_id")
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
            try:
                if llm_task.task_kind == LlmTaskKind.SHARE_ACTION.value:
                    run_share_action_task(db, llm_task_id=llm_task_id_int)
                else:
                    return TaskResult.fail(
                        f"Unsupported LLM task kind: {llm_task.task_kind}",
                        retryable=False,
                    )
            except LlmTaskError as exc:
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
                return TaskResult.fail(str(exc), retryable=False)

        return TaskResult.ok()
