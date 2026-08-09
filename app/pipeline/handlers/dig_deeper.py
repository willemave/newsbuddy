"""Dig-deeper task handler."""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.contracts import MessageProcessingStatus
from app.models.db import Content
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.chat_agent import update_message_failed
from app.services.dig_deeper import (
    InactiveDigDeeperUserError,
    prepare_dig_deeper_task_message,
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

        if (
            not isinstance(content_id, int)
            or isinstance(content_id, bool)
            or content_id <= 0
            or not isinstance(user_id, int)
            or isinstance(user_id, bool)
            or user_id <= 0
        ):
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
            content = db.query(Content).filter(Content.id == content_id).first()
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

            try:
                session_id, message_id, prompt, message_status, turn_context = (
                    prepare_dig_deeper_task_message(
                        db,
                        task_id=task.id,
                        content=content,
                        user_id=user_id,
                        initial_message=initial_message,
                    )
                )
            except InactiveDigDeeperUserError:
                logger.info(
                    "Skipping dig-deeper task for missing or inactive user",
                    extra={
                        "component": "dig_deeper",
                        "operation": "create_message",
                        "item_id": content_id,
                        "context_data": {"user_id": user_id},
                    },
                )
                return TaskResult.ok()
            except ValueError as exc:
                logger.error(
                    "DIG_DEEPER_TASK_ERROR: Invalid persisted task state",
                    extra={
                        "component": "dig_deeper",
                        "operation": "prepare_message",
                        "item_id": content_id,
                        "context_data": {"user_id": user_id, "error": str(exc)},
                    },
                )
                return TaskResult.fail(str(exc), retryable=False)

        if message_status is MessageProcessingStatus.COMPLETED:
            return TaskResult.ok()
        if message_status is MessageProcessingStatus.FAILED:
            return TaskResult.fail(
                "Dig-deeper message already failed",
                retryable=False,
            )

        try:
            run_dig_deeper_message(
                session_id,
                message_id,
                prompt,
                task_id=task.id,
                turn_context=turn_context,
            )
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
            terminal_state_persisted = False
            try:
                with context.db_factory() as db:
                    update_message_failed(
                        db,
                        message_id,
                        str(exc),
                        commit=False,
                    )
                terminal_state_persisted = True
            except Exception:  # noqa: BLE001
                logger.exception(
                    "DIG_DEEPER_TASK_ERROR: Failed to mark message terminal",
                    extra={
                        "component": "dig_deeper",
                        "operation": "fail_message",
                        "item_id": content_id,
                        "context_data": {
                            "session_id": session_id,
                            "message_id": message_id,
                        },
                    },
                )
            return TaskResult.fail(
                str(exc),
                retryable=not terminal_state_persisted,
            )

        return TaskResult.ok()
