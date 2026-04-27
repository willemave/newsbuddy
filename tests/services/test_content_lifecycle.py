"""Tests for centralized content lifecycle decisions."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.contracts import ContentStatus, ContentType, TaskType
from app.models.metadata import ContentData
from app.services.content_lifecycle import (
    build_content_lifecycle_log_extra,
    complete_with_reused_summary,
    decide_process_content_lifecycle,
    next_task_after_processing,
    process_content_lifecycle_event_names,
)


def _content(
    *,
    content_type: ContentType,
    status: ContentStatus,
    metadata: dict[str, object],
) -> ContentData:
    return ContentData(
        id=123,
        url="https://example.com/item",
        content_type=content_type,
        status=status,
        metadata=metadata,
        title="Example",
        created_at=datetime.now(UTC),
        processed_at=None,
        retry_count=0,
    )


def test_article_extraction_queues_summarize() -> None:
    content = _content(
        content_type=ContentType.ARTICLE,
        status=ContentStatus.PROCESSING,
        metadata={"content_to_summarize": "Article body"},
    )

    decision = decide_process_content_lifecycle(
        content=content,
        success=True,
        starting_metadata={},
    )

    assert decision.transition.reason == "processed.awaiting_summarization"
    assert decision.enqueue_summarize_task is True
    assert decision.next_task_type is None
    assert next_task_after_processing(content) == TaskType.SUMMARIZE


def test_podcast_transcript_queues_summarize() -> None:
    content = _content(
        content_type=ContentType.PODCAST,
        status=ContentStatus.PROCESSING,
        metadata={"transcript": "Podcast transcript"},
    )

    decision = decide_process_content_lifecycle(
        content=content,
        success=True,
        starting_metadata={},
    )

    assert decision.enqueue_summarize_task is True
    assert next_task_after_processing(content) == TaskType.PROCESS_PODCAST_MEDIA


def test_twitter_video_can_run_before_summary() -> None:
    content = _content(
        content_type=ContentType.NEWS,
        status=ContentStatus.PROCESSING,
        metadata={
            "article": {"url": "https://x.com/i/status/123"},
            "content_to_summarize": "Tweet body",
            "has_video": True,
        },
    )

    decision = decide_process_content_lifecycle(
        content=content,
        success=True,
        starting_metadata={},
        next_task_type=TaskType.DOWNLOAD_TWEET_VIDEO_AUDIO,
    )

    assert decision.enqueue_summarize_task is True
    assert decision.next_task_type == TaskType.DOWNLOAD_TWEET_VIDEO_AUDIO
    assert decision.reuse_existing_summary is False


def test_failed_extraction_becomes_terminal_failure() -> None:
    content = _content(
        content_type=ContentType.ARTICLE,
        status=ContentStatus.PROCESSING,
        metadata={},
    )

    decision = decide_process_content_lifecycle(
        content=content,
        success=False,
        starting_metadata={},
    )

    assert decision.transition.to_status == ContentStatus.FAILED
    assert decision.transition.reason == "processed.failure"
    assert decision.terminal_failure_status == ContentStatus.FAILED
    assert decision.enqueue_summarize_task is False


def test_reused_summary_completes_long_form_to_awaiting_image() -> None:
    content = _content(
        content_type=ContentType.ARTICLE,
        status=ContentStatus.PROCESSING,
        metadata={"content_to_summarize": "Same article body"},
    )
    starting_metadata = {
        "content_to_summarize": "Same article body",
        "summary": {
            "title": "Existing Summary",
            "overview": "Already summarized.",
            "bullet_points": [],
            "topics": [],
            "classification": "to_read",
        },
    }

    decision = decide_process_content_lifecycle(
        content=content,
        success=True,
        starting_metadata=starting_metadata,
    )
    complete_with_reused_summary(content)

    assert decision.reuse_existing_summary is True
    assert content.status == ContentStatus.AWAITING_IMAGE
    assert isinstance(content.metadata.get("summarization_input_fingerprint"), str)


def test_process_content_lifecycle_events_include_extracted_and_summarize_queued() -> None:
    content = _content(
        content_type=ContentType.ARTICLE,
        status=ContentStatus.PROCESSING,
        metadata={"content_to_summarize": "Article body"},
    )

    decision = decide_process_content_lifecycle(
        content=content,
        success=True,
        starting_metadata={},
    )

    assert process_content_lifecycle_event_names(
        decision=decision,
        success=True,
        final_status=ContentStatus.PROCESSING,
        queued_task_type=TaskType.SUMMARIZE,
    ) == ["content.extracted", "content.summarize_queued"]


def test_process_content_lifecycle_events_report_terminal_failure() -> None:
    content = _content(
        content_type=ContentType.ARTICLE,
        status=ContentStatus.PROCESSING,
        metadata={},
    )

    decision = decide_process_content_lifecycle(
        content=content,
        success=False,
        starting_metadata={},
    )

    assert process_content_lifecycle_event_names(
        decision=decision,
        success=False,
        final_status=ContentStatus.FAILED,
        queued_task_type=None,
    ) == ["content.failed"]


def test_build_content_lifecycle_log_extra_includes_structured_context() -> None:
    content = _content(
        content_type=ContentType.ARTICLE,
        status=ContentStatus.PROCESSING,
        metadata={"content_to_summarize": "Article body"},
    )
    decision = decide_process_content_lifecycle(
        content=content,
        success=True,
        starting_metadata={},
    )

    extra = build_content_lifecycle_log_extra(
        event_name="content.summarize_queued",
        operation="process_content",
        content_id=123,
        content_type=ContentType.ARTICLE,
        status=ContentStatus.PROCESSING,
        worker_id="worker-1",
        task_id=456,
        task_type=TaskType.SUMMARIZE,
        transition=decision.transition,
    )

    assert extra["event_name"] == "content.summarize_queued"
    assert extra["component"] == "content_lifecycle"
    assert extra["content_id"] == 123
    assert extra["worker_id"] == "worker-1"
    assert extra["task_id"] == 456
    assert extra["task_type"] == "summarize"
    assert extra["status"] == "processing"
    assert extra["context_data"] == {
        "content_type": "article",
        "transition_from": "processing",
        "transition_to": "processing",
        "transition_reason": "processed.awaiting_summarization",
    }
