"""Workflow orchestration for content processing transitions."""

from __future__ import annotations

from app.models.contracts import ContentStatus, TaskType
from app.models.metadata import ContentData
from app.services.content_lifecycle import (
    TERMINAL_STATUSES as CONTENT_TERMINAL_STATUSES,
)
from app.services.content_lifecycle import (
    WorkflowTransition,
    infer_process_transition,
    next_task_after_processing,
    should_enqueue_summarize_after_processing,
)


class ContentProcessingWorkflow:
    """Derives canonical state transitions during `process_content`."""

    TERMINAL_STATUSES: set[ContentStatus] = CONTENT_TERMINAL_STATUSES

    def infer_transition(
        self,
        *,
        content: ContentData,
        success: bool,
    ) -> WorkflowTransition:
        """Return status transition metadata for a processing result."""
        return infer_process_transition(content=content, success=success)

    def should_enqueue_summarize(self, content: ContentData) -> bool:
        """Return True when content should enqueue `SUMMARIZE` next step."""
        return should_enqueue_summarize_after_processing(content)

    @staticmethod
    def next_task_type(content: ContentData) -> TaskType | None:
        """Return the next task type for processed content."""
        return next_task_after_processing(content)
