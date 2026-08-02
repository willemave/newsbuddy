"""Queue handler for durable account deletion."""

from __future__ import annotations

from app.core.db import get_db
from app.models.contracts import TaskType
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.account_deletion import cancel_pending_user_tasks, purge_user_account


class DeleteUserAccountHandler:
    task_type = TaskType.DELETE_USER_ACCOUNT

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        del context
        user_id = task.payload.get("user_id")
        if not isinstance(user_id, int):
            return TaskResult.fail("Missing user_id", retryable=False)
        with get_db() as db:
            if not cancel_pending_user_tasks(db, user_id=user_id, current_task_id=task.id):
                return TaskResult.defer(retry_delay_seconds=30)
            purge_user_account(db, user_id=user_id, current_task_id=task.id)
        return TaskResult.ok()
