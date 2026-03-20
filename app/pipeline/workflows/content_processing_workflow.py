"""Workflow orchestration for content processing transitions."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.contracts import ContentStatus, ContentType, TaskType
from app.models.metadata import ContentData


@dataclass(frozen=True)
class WorkflowTransition:
    """Represents a high-level processing transition."""

    from_status: ContentStatus
    to_status: ContentStatus
    reason: str


class ContentProcessingWorkflow:
    """Derives canonical state transitions during `process_content`."""

    TERMINAL_STATUSES: set[ContentStatus] = {
        ContentStatus.FAILED,
        ContentStatus.SKIPPED,
    }

    def infer_transition(
        self,
        *,
        content: ContentData,
        success: bool,
    ) -> WorkflowTransition:
        """Return status transition metadata for a processing result."""
        previous_status = content.status

        if success and content.status == ContentStatus.PROCESSING:
            return WorkflowTransition(
                from_status=previous_status,
                to_status=ContentStatus.PROCESSING,
                reason="processed.awaiting_summarization",
            )

        if content.status in self.TERMINAL_STATUSES:
            return WorkflowTransition(
                from_status=previous_status,
                to_status=content.status,
                reason="processed.terminal",
            )

        if success:
            return WorkflowTransition(
                from_status=previous_status,
                to_status=content.status,
                reason="processed.success",
            )

        return WorkflowTransition(
            from_status=previous_status,
            to_status=ContentStatus.FAILED,
            reason="processed.failure",
        )

    def should_enqueue_summarize(self, content: ContentData) -> bool:
        """Return True when content should enqueue `SUMMARIZE` next step."""
        if content.status != ContentStatus.PROCESSING:
            return False

        if content.content_type in {ContentType.ARTICLE, ContentType.NEWS}:
            summary_payload = content.metadata.get("content_to_summarize")
            return isinstance(summary_payload, str) and bool(summary_payload.strip())

        if content.content_type == ContentType.PODCAST:
            transcript = content.metadata.get("transcript")
            if isinstance(transcript, str) and transcript.strip():
                return True
            summary_payload = content.metadata.get("content_to_summarize")
            return isinstance(summary_payload, str) and bool(summary_payload.strip())

        return False

    @staticmethod
    def next_task_type(content: ContentData) -> TaskType | None:
        """Return the next task type for processed content."""
        if content.content_type in {ContentType.ARTICLE, ContentType.NEWS}:
            return TaskType.SUMMARIZE
        if content.content_type == ContentType.PODCAST:
            return TaskType.DOWNLOAD_AUDIO
        return None
