"""Summarization task handler."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from app.constants import (
    SUMMARY_KIND_LONG_BULLETS,
    SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE,
    SUMMARY_KIND_LONG_INTERLEAVED,
    SUMMARY_KIND_LONG_STRUCTURED,
    SUMMARY_KIND_SHORT_NEWS,
    SUMMARY_VERSION_V1,
    SUMMARY_VERSION_V2,
)
from app.core.logging import get_logger
from app.models.metadata import (
    BulletedSummary,
    ContentStatus,
    ContentType,
    EditorialNarrativeSummary,
    InterleavedSummary,
    InterleavedSummaryV2,
    NewsSummary,
)
from app.models.schema import Content
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.content_bodies import get_content_body_resolver, sync_content_body_storage
from app.services.content_metadata_merge import refresh_merge_content_metadata
from app.services.content_status_state_machine import ContentStatusStateMachine
from app.services.dig_deeper import enqueue_dig_deeper_task
from app.services.long_form_images import (
    enqueue_visible_long_form_image_if_needed,
    has_generated_long_form_image,
)
from app.services.queue import TaskType
from app.services.summarization_templates import (
    resolve_editorial_summary_version,
    resolve_summarization_prompt_route,
)
from app.utils.summarization_inputs import (
    build_summarization_payload,
    compute_summarization_input_fingerprint,
)

logger = get_logger(__name__)

RETRYABLE_SUMMARIZATION_TOKENS = (
    "timeout",
    "timed out",
    "rate limit",
    "too many requests",
    "429",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "connection reset",
    "connection refused",
    "connection aborted",
    "resource exhausted",
    "precondition",
    "overloaded",
)


def _is_retryable_summarization_error(exc: Exception) -> bool:
    """Return True when summarize failure looks transient and should retry."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    message = str(exc).lower()
    return any(token in message for token in RETRYABLE_SUMMARIZATION_TOKENS)


def _extract_share_and_chat_user_ids(metadata: dict[str, Any]) -> list[int]:
    """Extract share-and-chat user IDs from metadata if present."""
    raw_users = metadata.get("share_and_chat_user_ids")
    user_ids: list[int] = []

    if isinstance(raw_users, list):
        for value in raw_users:
            try:
                user_ids.append(int(value))
            except (TypeError, ValueError):
                continue
    elif raw_users is not None:
        try:
            user_ids.append(int(raw_users))
        except (TypeError, ValueError):
            user_ids = []

    return [user_id for user_id in user_ids if user_id > 0]


class SummarizeHandler:
    """Handle content summarization tasks."""

    task_type = TaskType.SUMMARIZE

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Generate summaries and queue follow-up tasks."""
        try:
            content_id = task.content_id or task.payload.get("content_id")

            if not content_id:
                logger.error(
                    "SUMMARIZE_TASK_ERROR: No content_id provided.",
                    extra={
                        "component": "summarization",
                        "operation": "summarize_task",
                        "item_id": None,
                        "context_data": {"task_data": task.model_dump()},
                    },
                )
                return TaskResult.fail("No content_id provided", retryable=False)

            logger.info("Processing summarize task for content %s", content_id)

            with context.db_factory() as db:
                content = db.query(Content).filter(Content.id == content_id).first()
                if not content:
                    logger.error(
                        "SUMMARIZE_TASK_ERROR: Content %s not found in database",
                        content_id,
                        extra={
                            "component": "summarization",
                            "operation": "load_content",
                            "item_id": content_id,
                            "context_data": {"content_id": content_id},
                        },
                    )
                    return TaskResult.fail("Content not found", retryable=False)

                title_preview = "No title"
                if content.title and isinstance(content.title, str):
                    title_preview = content.title[:50]
                logger.info(
                    "Summarizing content %s: type=%s, title=%s, url=%s, status=%s",
                    content_id,
                    content.content_type,
                    title_preview,
                    content.url,
                    content.status,
                )
                metadata = (
                    content.content_metadata if isinstance(content.content_metadata, dict) else {}
                )
                terminal_statuses = {
                    ContentStatus.FAILED.value,
                    ContentStatus.SKIPPED.value,
                }
                if content.status in terminal_statuses:
                    logger.info(
                        "Skipping summarize task for content %s due to terminal status=%s",
                        content_id,
                        content.status,
                    )
                    return TaskResult.ok()
                if content.status == ContentStatus.COMPLETED.value and isinstance(
                    metadata.get("summary"), dict
                ):
                    logger.info(
                        "Skipping summarize task for content %s; summary already exists",
                        content_id,
                    )
                    return TaskResult.ok()

                def _load_latest_metadata() -> dict[str, Any]:
                    db.refresh(content)
                    latest_metadata = content.content_metadata
                    if not isinstance(latest_metadata, dict):
                        return {}
                    return dict(latest_metadata)

                body_resolver = get_content_body_resolver()

                def _persist_failure(
                    reason: str,
                    *,
                    status: ContentStatus = ContentStatus.FAILED,
                ) -> None:
                    base_metadata = _load_latest_metadata()
                    metadata = dict(base_metadata)
                    metadata.pop("summary", None)
                    existing_errors = metadata.get("processing_errors")
                    processing_errors = (
                        existing_errors.copy() if isinstance(existing_errors, list) else []
                    )
                    processing_errors.append(
                        {
                            "stage": "summarization",
                            "reason": reason,
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                    )
                    metadata["processing_errors"] = processing_errors

                    content.content_metadata = refresh_merge_content_metadata(
                        db,
                        content_id=content.id,
                        base_metadata=base_metadata,
                        updated_metadata=metadata,
                    )
                    content.status = status.value
                    content.error_message = reason[:500]
                    content.processed_at = datetime.now(UTC)
                    db.commit()

                def _persist_retryable_failure(reason: str) -> None:
                    base_metadata = _load_latest_metadata()
                    metadata = dict(base_metadata)
                    existing_errors = metadata.get("processing_errors")
                    processing_errors = (
                        existing_errors.copy() if isinstance(existing_errors, list) else []
                    )
                    processing_errors.append(
                        {
                            "stage": "summarization",
                            "reason": reason,
                            "retryable": True,
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                    )
                    metadata["processing_errors"] = processing_errors

                    content.content_metadata = refresh_merge_content_metadata(
                        db,
                        content_id=content.id,
                        base_metadata=base_metadata,
                        updated_metadata=metadata,
                    )
                    content.status = ContentStatus.PROCESSING.value
                    content.error_message = reason[:500]
                    db.commit()

                if content.content_type not in {
                    ContentType.ARTICLE.value,
                    ContentType.NEWS.value,
                    ContentType.PODCAST.value,
                }:
                    reason = f"Unknown content type for summarization: {content.content_type}"
                    logger.error(
                        "SUMMARIZE_TASK_ERROR: %s. Content %s, URL: %s",
                        reason,
                        content_id,
                        content.url,
                        extra={
                            "component": "summarization",
                            "operation": "summarize_task",
                            "item_id": content_id,
                            "context_data": {
                                "content_type": content.content_type,
                                "url": str(content.url),
                                "title": content.title,
                            },
                        },
                    )
                    _persist_failure(reason)
                    return TaskResult.fail(reason, retryable=False)

                source_text = body_resolver.resolve_text(db, content=content)
                text_to_summarize = build_summarization_payload(
                    content.content_type,
                    metadata,
                    source_text=source_text,
                )

                if not text_to_summarize:
                    expected_field = (
                        "transcript" if content.content_type == "podcast" else "content"
                    )
                    reason = f"No text to summarize for content {content_id}"
                    logger.warning(
                        "SUMMARIZE_TASK_ERROR: %s. Type: %s, expected field: %s, "
                        "metadata keys: %s, URL: %s",
                        reason,
                        content.content_type,
                        expected_field,
                        list(metadata.keys()),
                        content.url,
                        extra={
                            "component": "summarization",
                            "operation": "summarize_task",
                            "item_id": content_id,
                            "context_data": {
                                "content_type": content.content_type,
                                "expected_field": expected_field,
                                "metadata_keys": list(metadata.keys()),
                                "url": str(content.url),
                                "title": content.title,
                            },
                        },
                    )
                    _persist_failure(reason, status=ContentStatus.SKIPPED)
                    return TaskResult.ok()

                logger.debug(
                    "Content %s has %d characters to summarize",
                    content_id,
                    len(text_to_summarize),
                )
                input_fingerprint = compute_summarization_input_fingerprint(
                    content.content_type,
                    text_to_summarize,
                )
                if (
                    isinstance(metadata.get("summary"), dict)
                    and metadata.get("summarization_input_fingerprint") == input_fingerprint
                ):
                    latest_metadata = _load_latest_metadata()
                    merged_metadata = dict(latest_metadata)
                    merged_metadata["summarization_input_fingerprint"] = input_fingerprint
                    content.content_metadata = refresh_merge_content_metadata(
                        db,
                        content_id=content.id,
                        base_metadata=latest_metadata,
                        updated_metadata=merged_metadata,
                    )
                    content.status = ContentStatusStateMachine.status_after_summary(
                        content_type=content.content_type,
                        artwork_ready=has_generated_long_form_image(content),
                    ).value
                    content.processed_at = datetime.now(UTC)
                    db.commit()
                    logger.info(
                        "Skipping summarize task for content %s; summarization input unchanged",
                        content_id,
                    )
                    return TaskResult.ok()

                summarization_type, max_bullet_points, max_quotes = (
                    resolve_summarization_prompt_route(
                        content.content_type,
                        url=content.url,
                        platform=content.platform,
                        metadata=metadata,
                    )
                )
                provider_override = None

                logger.info(
                    "Calling LLM for content %s: provider=%s, type=%s, "
                    "text_length=%d, max_bullets=%d",
                    content_id,
                    provider_override or "default",
                    summarization_type,
                    len(text_to_summarize),
                    max_bullet_points,
                )

                try:
                    summary = context.llm_service.summarize(
                        text_to_summarize,
                        content_type=summarization_type,
                        content_id=content.id,
                        max_bullet_points=max_bullet_points,
                        max_quotes=max_quotes,
                        provider_override=provider_override,
                        db=db,
                        usage_persist={
                            "feature": "summarization",
                            "operation": "summarization.llm_summarization",
                            "source": "queue",
                            "task_id": task.id,
                            "content_id": content.id,
                            "metadata": {
                                "content_type": content.content_type,
                                "summarization_type": summarization_type,
                            },
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "SUMMARIZE_TASK_ERROR: LLM call failed for content %s (%s). "
                        "Error: %s, URL: %s, text_length: %d",
                        content_id,
                        content.content_type,
                        str(exc),
                        content.url,
                        len(text_to_summarize),
                        extra={
                            "component": "summarization",
                            "operation": "llm_summarization",
                            "item_id": content_id,
                            "context_data": {
                                "content_type": content.content_type,
                                "summarization_type": summarization_type,
                                "provider": provider_override or "default",
                                "text_length": len(text_to_summarize),
                                "url": str(content.url),
                                "title": content.title,
                            },
                        },
                    )
                    failure_reason = f"Summarization error: {exc}"
                    if _is_retryable_summarization_error(exc):
                        _persist_retryable_failure(failure_reason)
                        return TaskResult.fail(str(exc), retryable=True)

                    _persist_failure(failure_reason)
                    return TaskResult.fail(str(exc), retryable=False)

                if summary is not None:
                    base_metadata = _load_latest_metadata()
                    metadata = dict(base_metadata)
                    share_and_chat_user_ids = _extract_share_and_chat_user_ids(metadata)
                    summary_dict = (
                        summary.model_dump(mode="json", by_alias=True)
                        if hasattr(summary, "model_dump")
                        else summary
                    )

                    if isinstance(summary, NewsSummary):
                        summary_kind = SUMMARY_KIND_SHORT_NEWS
                        summary_version = SUMMARY_VERSION_V1
                    elif isinstance(summary, EditorialNarrativeSummary):
                        summary_kind = SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE
                        summary_version = resolve_editorial_summary_version(summarization_type)
                    elif isinstance(summary, BulletedSummary):
                        summary_kind = SUMMARY_KIND_LONG_BULLETS
                        summary_version = SUMMARY_VERSION_V1
                    elif isinstance(summary, InterleavedSummaryV2):
                        summary_kind = SUMMARY_KIND_LONG_INTERLEAVED
                        summary_version = SUMMARY_VERSION_V2
                    elif isinstance(summary, InterleavedSummary):
                        summary_kind = SUMMARY_KIND_LONG_INTERLEAVED
                        summary_version = SUMMARY_VERSION_V1
                    else:
                        summary_kind = SUMMARY_KIND_LONG_STRUCTURED
                        summary_version = SUMMARY_VERSION_V1

                    metadata["summary_kind"] = summary_kind
                    metadata["summary_version"] = summary_version

                    if isinstance(summary, NewsSummary):
                        summary_dict.setdefault("classification", summary.classification)
                        metadata["summary"] = summary_dict

                        article_section = metadata.get("article", {})
                        article_section.setdefault(
                            "url",
                            summary_dict.get("final_url_after_redirects")
                            or summary_dict.get("article", {}).get("url"),
                        )
                        if summary.title and not article_section.get("title"):
                            article_section["title"] = summary.title
                        metadata["article"] = article_section

                        if summary.title:
                            content.title = summary.title

                        logger.info(
                            "Generated news summary for content %s",
                            content_id,
                        )
                    else:
                        metadata["summary"] = summary_dict
                        if summary_dict.get("title") and not content.title:
                            content.title = summary_dict["title"]
                        logger.info("Generated summary for content %s", content_id)

                    metadata["summarization_date"] = datetime.now(UTC).isoformat()
                    metadata["summarization_input_fingerprint"] = input_fingerprint
                    if share_and_chat_user_ids:
                        metadata.pop("share_and_chat_user_ids", None)

                    content.content_metadata = refresh_merge_content_metadata(
                        db,
                        content_id=content.id,
                        base_metadata=base_metadata,
                        updated_metadata=metadata,
                    )
                    sync_content_body_storage(db, content=content)
                    content.status = ContentStatusStateMachine.status_after_summary(
                        content_type=content.content_type,
                        artwork_ready=has_generated_long_form_image(content),
                    ).value
                    content.processed_at = datetime.now(UTC)
                    db.commit()

                    if share_and_chat_user_ids:
                        for user_id in share_and_chat_user_ids:
                            enqueue_dig_deeper_task(db, content_id, user_id)
                        logger.info(
                            "Enqueued dig-deeper tasks for content %s (users=%s)",
                            content_id,
                            share_and_chat_user_ids,
                        )

                    if content.content_type == ContentType.NEWS.value:
                        logger.info(
                            "Skipping post-summary image generation for news content %s",
                            content_id,
                        )
                    elif (
                        enqueue_visible_long_form_image_if_needed(
                            db,
                            content,
                            queue_service=cast(Any, context.queue),
                        )
                        is not None
                    ):
                        logger.info("Enqueued image generation for content %s", content_id)
                    else:
                        logger.info(
                            "Skipping post-summary image generation for content %s; "
                            "not visible or already covered",
                            content_id,
                        )

                    return TaskResult.ok()

                reason = "LLM summarization returned None"
                logger.error(
                    "MISSING_SUMMARY: Content %s (%s) - %s. Title: %s, Text length: %s, URL: %s",
                    content_id,
                    content.content_type,
                    reason,
                    content.title,
                    len(text_to_summarize) if text_to_summarize else 0,
                    content.url,
                    extra={
                        "component": "summarization",
                        "operation": "llm_summarization",
                        "item_id": content_id,
                        "context_data": {
                            "content_type": content.content_type,
                            "summarization_type": summarization_type,
                            "provider": provider_override or "default",
                            "text_length": len(text_to_summarize) if text_to_summarize else 0,
                            "url": str(content.url),
                            "title": content.title,
                        },
                    },
                )
                _persist_failure(reason)
                return TaskResult.fail(reason, retryable=False)

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Summarization error",
                extra={
                    "component": "summarization",
                    "operation": "summarize_task",
                    "item_id": task.content_id if task else None,
                    "context_data": {"task_data": task.model_dump() if task else None},
                },
            )
            return TaskResult.fail(str(exc))
