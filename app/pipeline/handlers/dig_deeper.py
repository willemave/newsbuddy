"""Dig-deeper task handler."""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.db import Content
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.dig_deeper import (
    create_dig_deeper_message,
    run_dig_deeper_message,
)
from app.services.queue import TaskType

logger = get_logger(__name__)


class DigDeeperHandler:
    """Handle dig-deeper chat tasks."""

    task_type = TaskType.DIG_DEEPER

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Start a dig-deeper chat for processed content."""
        payload = task.payload if isinstance(task.payload, dict) else {}
        content_id = task.content_id or payload.get("content_id")
        user_id = payload.get("user_id")
        raw_initial_message = payload.get("initial_message")
        initial_message = raw_initial_message if isinstance(raw_initial_message, str) else None

        if not content_id or not user_id:
            logger.error(
                "DIG_DEEPER_TASK_ERROR: Missing content_id or user_id (content_id=%s, user_id=%s)",
                content_id,
                user_id,
                extra={
                    "component": "dig_deeper",
                    "operation": "process_task",
                    "context_data": {"content_id": content_id, "user_id": user_id},
                },
            )
            return TaskResult.fail("Missing content_id or user_id")

        with context.db_factory() as db:
            content = db.query(Content).filter(Content.id == int(content_id)).first()
            if not content:
                logger.error(
                    "DIG_DEEPER_TASK_ERROR: Content %s not found",
                    content_id,
                    extra={
                        "component": "dig_deeper",
                        "operation": "load_content",
                        "item_id": content_id,
                    },
                )
                return TaskResult.fail("Content not found")

            session_id, message_id, prompt = create_dig_deeper_message(
                db,
                content,
                int(user_id),
                initial_message=initial_message,
            )

        try:
            run_dig_deeper_message(session_id, message_id, prompt, task_id=task.id)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "DIG_DEEPER_TASK_ERROR: Failed to process message for content %s",
                content_id,
                extra={
                    "component": "dig_deeper",
                    "operation": "process_message",
                    "item_id": content_id,
                    "context_data": {
                        "session_id": session_id,
                        "message_id": message_id,
                        "error": str(exc),
                    },
                },
            )
            return TaskResult.fail(str(exc))

        return TaskResult.ok()
