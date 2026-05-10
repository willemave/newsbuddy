"""Content processing task handler."""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.contracts import ContentStatus
from app.models.db import Content
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.pipeline.worker import ContentWorker
from app.services.queue import TaskType

logger = get_logger(__name__)


class ProcessContentHandler:
    """Handle content processing tasks."""

    task_type = TaskType.PROCESS_CONTENT

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Process content with registered strategies."""
        try:
            content_id = task.content_id
            if content_id is None:
                content_id = task.payload.get("content_id")

            if content_id is None:
                logger.error(
                    "No content_id found in task data",
                    extra={
                        "component": "process_content",
                        "operation": "load_task",
                        "item_id": None,
                        "context_data": {"task_data": task.model_dump()},
                    },
                )
                return TaskResult.fail("No content_id provided", retryable=False)

            content_id = int(content_id)
            logger.info("Processing content %s", content_id)

            worker = ContentWorker()
            success = worker.process_content(content_id, context.worker_id)

            if success:
                logger.info("Content %s processed successfully", content_id)
                return TaskResult.ok()

            with context.db_factory() as db:
                content_row = (
                    db.query(Content.status, Content.error_message)
                    .filter(Content.id == content_id)
                    .first()
                )

            if content_row:
                status, _error_message = content_row
                terminal_statuses = {
                    ContentStatus.FAILED.value,
                    ContentStatus.SKIPPED.value,
                    ContentStatus.COMPLETED.value,
                }
                if status in terminal_statuses:
                    logger.info(
                        "Content %s ended in terminal status=%s; not retrying process task",
                        content_id,
                        status,
                    )
                    # Processing reached a terminal state; task itself is complete.
                    return TaskResult.ok()

            logger.error(
                "Content %s processing failed",
                content_id,
                extra={
                    "component": "process_content",
                    "operation": "process_content",
                    "item_id": content_id,
                    "context_data": {"task_id": task.id},
                },
            )
            return TaskResult.fail()
        except Exception as exc:  # noqa: BLE001
            resolved_content_id = content_id if "content_id" in locals() else None
            logger.exception(
                "Content processing error",
                extra={
                    "component": "process_content",
                    "operation": "process_content",
                    "item_id": resolved_content_id,
                    "context_data": {"task_data": task.model_dump()},
                },
            )
            return TaskResult.fail(str(exc))
