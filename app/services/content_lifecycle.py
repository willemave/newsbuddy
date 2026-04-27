"""Central content-processing lifecycle decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.observability import build_log_extra
from app.models.contracts import ContentStatus, ContentType, TaskType
from app.models.metadata import ContentData
from app.services.content_status_state_machine import ContentStatusStateMachine
from app.utils.summarization_inputs import (
    build_summarization_payload,
    compute_summarization_input_fingerprint,
)

TERMINAL_STATUSES: set[ContentStatus] = {
    ContentStatus.FAILED,
    ContentStatus.SKIPPED,
}


@dataclass(frozen=True)
class WorkflowTransition:
    """High-level processing transition metadata."""

    from_status: ContentStatus
    to_status: ContentStatus
    reason: str


@dataclass(frozen=True)
class ProcessContentLifecycleDecision:
    """Post-extraction actions for `process_content`."""

    transition: WorkflowTransition
    terminal_failure_status: ContentStatus | None
    enqueue_summarize_task: bool
    next_task_type: TaskType | None
    reuse_existing_summary: bool


def build_content_lifecycle_log_extra(
    *,
    event_name: str,
    operation: str,
    content_id: int | None,
    content_type: ContentType | str | None,
    status: ContentStatus | str | None,
    worker_id: str | None = None,
    task_id: int | None = None,
    task_type: TaskType | str | None = None,
    queue_name: str | None = None,
    transition: WorkflowTransition | None = None,
    context_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build structured log metadata for content lifecycle events."""
    normalized_status = status.value if isinstance(status, ContentStatus) else status
    normalized_content_type = (
        content_type.value if isinstance(content_type, ContentType) else content_type
    )
    normalized_task_type = task_type.value if isinstance(task_type, TaskType) else task_type
    merged_context = dict(context_data or {})
    if normalized_content_type is not None:
        merged_context.setdefault("content_type", normalized_content_type)
    if transition is not None:
        merged_context.update(
            {
                "transition_from": transition.from_status.value,
                "transition_to": transition.to_status.value,
                "transition_reason": transition.reason,
            }
        )

    return build_log_extra(
        component="content_lifecycle",
        operation=operation,
        event_name=event_name,
        status=normalized_status,
        item_id=content_id,
        context_data=merged_context or None,
        content_id=content_id,
        worker_id=worker_id,
        task_id=task_id,
        task_type=normalized_task_type,
        queue_name=queue_name,
    )


def process_content_lifecycle_event_names(
    *,
    decision: ProcessContentLifecycleDecision,
    success: bool,
    final_status: ContentStatus,
    queued_task_type: TaskType | None,
) -> list[str]:
    """Return lifecycle event names emitted after extraction processing."""
    if final_status == ContentStatus.FAILED:
        return ["content.failed"]

    event_names: list[str] = []
    if success and final_status == ContentStatus.PROCESSING:
        event_names.append("content.extracted")

    if decision.enqueue_summarize_task:
        if queued_task_type is None or queued_task_type == TaskType.SUMMARIZE:
            event_names.append("content.summarize_queued")
        elif queued_task_type == TaskType.PROCESS_PODCAST_MEDIA:
            event_names.append("content.media_queued")
        elif queued_task_type == TaskType.DOWNLOAD_TWEET_VIDEO_AUDIO:
            event_names.append("content.video_audio_queued")

    if decision.reuse_existing_summary:
        event_names.append("content.summary_completed")

    if final_status == ContentStatus.COMPLETED:
        event_names.append("content.completed")

    return event_names


def infer_process_transition(*, content: ContentData, success: bool) -> WorkflowTransition:
    """Return status transition metadata for a processing result."""
    previous_status = content.status

    if success and content.status == ContentStatus.PROCESSING:
        return WorkflowTransition(
            from_status=previous_status,
            to_status=ContentStatus.PROCESSING,
            reason="processed.awaiting_summarization",
        )

    if content.status in TERMINAL_STATUSES:
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


def should_enqueue_summarize_after_processing(content: ContentData) -> bool:
    """Return True when processed content should enqueue summarization."""
    if content.status != ContentStatus.PROCESSING:
        return False

    if content.content_type in {ContentType.ARTICLE, ContentType.NEWS}:
        if content.metadata.get("excerpt"):
            return True
        summary_payload = content.metadata.get("content_to_summarize")
        return isinstance(summary_payload, str) and bool(summary_payload.strip())

    if content.content_type == ContentType.PODCAST:
        transcript = content.metadata.get("transcript")
        if isinstance(transcript, str) and transcript.strip():
            return True
        if content.metadata.get("has_transcript"):
            return True
        summary_payload = content.metadata.get("content_to_summarize")
        return isinstance(summary_payload, str) and bool(summary_payload.strip())

    return False


def next_task_after_processing(content: ContentData) -> TaskType | None:
    """Return the default next task type after extraction."""
    if content.content_type in {ContentType.ARTICLE, ContentType.NEWS}:
        return TaskType.SUMMARIZE
    if content.content_type == ContentType.PODCAST:
        return TaskType.PROCESS_PODCAST_MEDIA
    return None


def should_reuse_existing_summary(
    content: ContentData,
    starting_metadata: dict[str, object],
) -> bool:
    """Return True when processed content already has a matching summary."""
    if not isinstance(starting_metadata.get("summary"), dict):
        return False

    current_payload = build_summarization_payload(content.content_type, content.metadata or {})
    previous_payload = build_summarization_payload(content.content_type, starting_metadata)
    if not current_payload or not previous_payload:
        return False

    current_fingerprint = compute_summarization_input_fingerprint(
        content.content_type,
        current_payload,
    )
    previous_fingerprint = starting_metadata.get("summarization_input_fingerprint")
    if isinstance(previous_fingerprint, str) and previous_fingerprint == current_fingerprint:
        return True

    previous_payload_fingerprint = compute_summarization_input_fingerprint(
        content.content_type,
        previous_payload,
    )
    return previous_payload_fingerprint == current_fingerprint


def decide_process_content_lifecycle(
    *,
    content: ContentData,
    success: bool,
    starting_metadata: dict[str, object],
    next_task_type: TaskType | None = None,
) -> ProcessContentLifecycleDecision:
    """Compute post-extraction lifecycle actions without mutating content."""
    transition = infer_process_transition(content=content, success=success)
    terminal_failure_status = (
        ContentStatus.FAILED if not success and content.status not in TERMINAL_STATUSES else None
    )

    enqueue_summarize_task = False
    reuse_existing_summary = False
    if success:
        enqueue_summarize_task = should_enqueue_summarize_after_processing(content)
        reuse_existing_summary = (
            enqueue_summarize_task
            and next_task_type is None
            and should_reuse_existing_summary(content, starting_metadata)
        )

    return ProcessContentLifecycleDecision(
        transition=transition,
        terminal_failure_status=terminal_failure_status,
        enqueue_summarize_task=enqueue_summarize_task,
        next_task_type=next_task_type if enqueue_summarize_task else None,
        reuse_existing_summary=reuse_existing_summary,
    )


def complete_with_reused_summary(content: ContentData) -> None:
    """Mark content complete/awaiting image when an existing summary is still valid."""
    current_payload = build_summarization_payload(content.content_type, content.metadata or {})
    if current_payload:
        content.metadata["summarization_input_fingerprint"] = (
            compute_summarization_input_fingerprint(
                content.content_type,
                current_payload,
            )
        )
    content.status = ContentStatusStateMachine.status_after_summary(
        content_type=content.content_type,
        artwork_ready=bool(content.metadata.get("image_generated_at")),
    )
    content.processed_at = datetime.now(UTC)
