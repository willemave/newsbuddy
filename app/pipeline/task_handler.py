"""Handler protocol and adapters for task processing."""

from __future__ import annotations

from typing import Protocol

from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.queue import TaskType


class TaskHandler(Protocol):
    """Protocol for task handlers."""

    task_type: TaskType

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Handle a task and return a TaskResult."""
