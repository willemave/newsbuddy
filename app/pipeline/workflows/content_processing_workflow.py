"""Workflow orchestration for content processing transitions."""

from __future__ import annotations

from app.models.contracts import ContentStatus, TaskType
from app.models.domain.content import ContentData
from app.services.content_lifecycle import (
    TERMINAL_STATUSES as CONTENT_TERMINAL_STATUSES,
)
from app.services.content_lifecycle import next_task_after_processing


class ContentProcessingWorkflow:
    """Derives canonical state transitions during `process_content`."""

    TERMINAL_STATUSES: set[ContentStatus] = CONTENT_TERMINAL_STATUSES

    @staticmethod
    def next_task_type(content: ContentData) -> TaskType | None:
        """Return the next task type for processed content."""
        return next_task_after_processing(content)
