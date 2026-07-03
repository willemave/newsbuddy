from __future__ import annotations

from app.core.logging import get_logger
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.briefing.refresh import RefreshMode, run_briefing_refresh
from app.services.queue import TaskType

logger = get_logger(__name__)


class BriefingRefreshHandler:
    """Build or append the user's unread briefing edition."""

    task_type = TaskType.BRIEFING_REFRESH

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        payload = task.payload if isinstance(task.payload, dict) else {}
        raw_user_id = payload.get("user_id")
        raw_mode = str(payload.get("mode") or "append")
        mode: RefreshMode
        if raw_mode == "append":
            mode = "append"
        elif raw_mode == "sweep":
            mode = "sweep"
        elif raw_mode == "full":
            mode = "full"
        else:
            return TaskResult.fail(f"Invalid briefing refresh mode: {raw_mode}", retryable=False)
        if raw_user_id is None:
            return TaskResult.fail("Missing or invalid user_id", retryable=False)
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            return TaskResult.fail("Missing or invalid user_id", retryable=False)

        with context.db_factory() as db:
            try:
                run_briefing_refresh(
                    db,
                    user_id=user_id,
                    mode=mode,
                    task_id=task.id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Briefing refresh failed",
                    extra={
                        "component": "briefing_refresh",
                        "operation": "handle",
                        "item_id": user_id,
                        "task_id": task.id,
                        "context_data": {"mode": mode, "error": str(exc)},
                    },
                )
                return TaskResult.fail(str(exc))
        return TaskResult.ok()
