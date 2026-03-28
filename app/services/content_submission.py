"""Helpers for user-submitted one-off content."""

from __future__ import annotations

from pydantic import HttpUrl, TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import SELF_SUBMISSION_SOURCE
from app.core.logging import get_logger
from app.models.content_submission import ContentSubmissionResponse, SubmitContentRequest
from app.models.metadata import ContentClassification, ContentStatus, ContentType
from app.models.metadata_state import normalize_metadata_shape, update_processing_state
from app.models.schema import Content, ProcessingTask
from app.models.user import User
from app.services import read_status
from app.services.dig_deeper import enqueue_dig_deeper_task
from app.services.long_form_images import enqueue_visible_long_form_image_if_needed
from app.services.queue import TaskQueue, TaskStatus, TaskType
from app.services.scraper_configs import ensure_inbox_status

# Re-export for backwards compatibility
from app.services.url_detection import (  # noqa: F401
    PLATFORMS_SKIP_LLM_ANALYSIS,
    PODCAST_HOST_PLATFORMS,
    PODCAST_PATH_KEYWORDS,
    infer_content_type_and_platform,
    should_use_llm_analysis,
)

logger = get_logger(__name__)

URL_ADAPTER = TypeAdapter(HttpUrl)
REANALYZE_EXISTING_STATUSES: set[str] = {
    ContentStatus.NEW.value,
    ContentStatus.PENDING.value,
    ContentStatus.FAILED.value,
    ContentStatus.SKIPPED.value,
}


def normalize_url(raw_url: str) -> str:
    """Normalize and validate the incoming URL string.

    Args:
        raw_url: URL provided by the client.

    Returns:
        Validated and normalized URL string.
    """
    return str(URL_ADAPTER.validate_python(raw_url)).strip()


def _ensure_analyze_url_task(
    db: Session,
    content_id: int,
    instruction: str | None = None,
    *,
    crawl_links: bool = False,
    subscribe_to_feed: bool = False,
) -> int:
    """Create an ANALYZE_URL task if one is not already pending/processing.

    Args:
        db: Active database session.
        content_id: Content identifier to analyze.
        instruction: Optional instruction for analysis.
        crawl_links: Whether to allow link crawling from the instruction analysis.

    Returns:
        ProcessingTask ID.
    """
    # Check for existing ANALYZE_URL or PROCESS_CONTENT task
    existing_task = (
        db.query(ProcessingTask)
        .filter(ProcessingTask.content_id == content_id)
        .filter(
            ProcessingTask.task_type.in_(
                [TaskType.ANALYZE_URL.value, TaskType.PROCESS_CONTENT.value]
            )
        )
        .filter(ProcessingTask.status.in_([TaskStatus.PENDING.value, TaskStatus.PROCESSING.value]))
        .first()
    )
    if existing_task:
        if existing_task.task_type == TaskType.ANALYZE_URL.value:
            payload = dict(existing_task.payload or {})
            payload.setdefault("content_id", content_id)
            updated = False
            cleaned_instruction = instruction.strip() if instruction else None
            if cleaned_instruction and payload.get("instruction") != cleaned_instruction:
                payload["instruction"] = cleaned_instruction
                updated = True
            if crawl_links and payload.get("crawl_links") is not True:
                payload["crawl_links"] = True
                updated = True
            if subscribe_to_feed and payload.get("subscribe_to_feed") is not True:
                payload["subscribe_to_feed"] = True
                updated = True
            if updated:
                existing_task.payload = payload
                db.commit()
        return existing_task.id

    payload: dict[str, object] = {"content_id": content_id}
    if instruction and instruction.strip():
        payload["instruction"] = instruction.strip()
    if crawl_links:
        payload["crawl_links"] = True
    if subscribe_to_feed:
        payload["subscribe_to_feed"] = True

    task = ProcessingTask(
        task_type=TaskType.ANALYZE_URL.value,
        content_id=content_id,
        payload=payload,
        status=TaskStatus.PENDING.value,
        queue_name=TaskQueue.CONTENT.value,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task.id


def _append_share_and_chat_user(
    metadata: dict[str, object] | None,
    user_id: int,
) -> dict[str, object]:
    """Append the user to pending share-and-chat metadata.

    Args:
        metadata: Existing content metadata.
        user_id: User requesting share-and-chat.

    Returns:
        Updated metadata dictionary.
    """
    updated = normalize_metadata_shape(dict(metadata or {}))
    raw_users = updated.get("share_and_chat_user_ids")
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

    if user_id not in user_ids:
        user_ids.append(user_id)

    return update_processing_state(updated, share_and_chat_user_ids=user_ids)


def _should_enqueue_analysis_for_existing_content(
    *,
    existing_status: str,
    instruction: str | None,
    crawl_links: bool,
    subscribe_to_feed: bool,
) -> bool:
    """Return True when a duplicate submission should enqueue ANALYZE_URL again."""
    if subscribe_to_feed or crawl_links or bool(instruction):
        return True
    return existing_status in REANALYZE_EXISTING_STATUSES


def submit_user_content(
    db: Session,
    payload: SubmitContentRequest,
    current_user: User,
    *,
    submitted_via: str = "share_sheet",
) -> ContentSubmissionResponse:
    """Persist and enqueue a user-submitted URL for async analysis.

    Creates content with UNKNOWN type and enqueues ANALYZE_URL task.
    The async task will determine content type (via pattern matching or LLM)
    and then enqueue PROCESS_CONTENT.

    Args:
        db: Active database session.
        payload: Submission request payload.
        current_user: Authenticated user submitting the URL.

    Returns:
        Submission response describing the created or existing content.
    """
    raw_url = str(payload.url)
    submission_channel = submitted_via.strip() or "share_sheet"
    normalized_url = normalize_url(raw_url)

    # Check if content already exists (by URL only, regardless of type)
    instruction = payload.instruction.strip() if payload.instruction else None
    crawl_links = payload.crawl_links
    subscribe_to_feed = payload.subscribe_to_feed
    share_and_chat = payload.share_and_chat and not subscribe_to_feed
    platform_hint = (payload.platform or "").strip().lower() or None

    existing = db.query(Content).filter(Content.url == normalized_url).first()
    if existing:
        source_url_updated = False
        metadata_updated = False
        if not existing.source_url:
            existing.source_url = raw_url
            source_url_updated = True
        if platform_hint and not existing.platform:
            existing.platform = platform_hint
            source_url_updated = True
        if subscribe_to_feed:
            existing_metadata = normalize_metadata_shape(dict(existing.content_metadata or {}))
            existing_metadata = update_processing_state(
                existing_metadata,
                subscribe_to_feed=True,
            )
            existing_metadata.setdefault("submitted_by_user_id", current_user.id)
            existing_metadata.setdefault("submitted_via", submission_channel)
            if platform_hint:
                existing_metadata.setdefault("platform_hint", platform_hint)
            existing.content_metadata = existing_metadata
            db.commit()
        else:
            if share_and_chat and existing.status != ContentStatus.COMPLETED.value:
                existing.content_metadata = _append_share_and_chat_user(
                    existing.content_metadata, current_user.id
                )
                metadata_updated = True
            status_created = ensure_inbox_status(
                db, current_user.id, existing.id, content_type=existing.content_type
            )
            if status_created or source_url_updated or metadata_updated:
                db.commit()
            if status_created:
                enqueue_visible_long_form_image_if_needed(db, existing)
            if share_and_chat:
                read_status.mark_content_as_read(db, existing.id, current_user.id)
                if existing.status == ContentStatus.COMPLETED.value:
                    enqueue_dig_deeper_task(db, existing.id, current_user.id)
        task_id: int | None = None
        if _should_enqueue_analysis_for_existing_content(
            existing_status=existing.status,
            instruction=instruction,
            crawl_links=crawl_links,
            subscribe_to_feed=subscribe_to_feed,
        ):
            task_id = _ensure_analyze_url_task(
                db,
                existing.id,
                instruction=instruction,
                crawl_links=crawl_links,
                subscribe_to_feed=subscribe_to_feed,
            )
        return ContentSubmissionResponse(
            content_id=existing.id,
            content_type=ContentType(existing.content_type),
            status=ContentStatus(existing.status),
            platform=existing.platform,
            already_exists=True,
            message=(
                "Feed subscription queued"
                if subscribe_to_feed
                else "Content already submitted; using existing record"
            ),
            task_id=task_id,
            source=existing.source or SELF_SUBMISSION_SOURCE,
        )

    # Build initial metadata
    metadata: dict[str, object] = {
        "source": SELF_SUBMISSION_SOURCE,
    }
    metadata = update_processing_state(
        metadata,
        submitted_by_user_id=current_user.id,
        submitted_via=submission_channel,
    )
    if subscribe_to_feed:
        metadata = update_processing_state(metadata, subscribe_to_feed=True)
    if platform_hint:
        metadata = update_processing_state(metadata, platform_hint=platform_hint)
    if share_and_chat:
        metadata = _append_share_and_chat_user(metadata, current_user.id)

    # Create content with UNKNOWN type - will be updated by ANALYZE_URL task
    new_content = Content(
        url=normalized_url,
        source_url=raw_url,
        content_type=ContentType.UNKNOWN.value,
        title=payload.title,
        source=SELF_SUBMISSION_SOURCE,
        platform=platform_hint,
        is_aggregate=False,
        status=ContentStatus.NEW.value,
        classification=ContentClassification.TO_READ.value,
        content_metadata=metadata,
    )

    db.add(new_content)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.warning("Self-submission hit duplicate constraint for %s: %s", normalized_url, exc)
        existing = db.query(Content).filter(Content.url == normalized_url).first()
        if not existing:
            raise
        task_id = _ensure_analyze_url_task(
            db,
            existing.id,
            instruction=instruction,
            crawl_links=crawl_links,
            subscribe_to_feed=subscribe_to_feed,
        )
        return ContentSubmissionResponse(
            content_id=existing.id,
            content_type=ContentType(existing.content_type),
            status=ContentStatus(existing.status),
            platform=existing.platform,
            already_exists=True,
            message=(
                "Feed subscription queued"
                if subscribe_to_feed
                else "Content already submitted; using existing record"
            ),
            task_id=task_id,
            source=existing.source or SELF_SUBMISSION_SOURCE,
        )

    db.refresh(new_content)
    if not subscribe_to_feed:
        status_created = ensure_inbox_status(
            db, current_user.id, new_content.id, content_type=new_content.content_type
        )
        if status_created:
            db.commit()
            enqueue_visible_long_form_image_if_needed(db, new_content)
        if share_and_chat:
            read_status.mark_content_as_read(db, new_content.id, current_user.id)
    task_id = _ensure_analyze_url_task(
        db,
        new_content.id,
        instruction=instruction,
        crawl_links=crawl_links,
        subscribe_to_feed=subscribe_to_feed,
    )

    return ContentSubmissionResponse(
        content_id=new_content.id,
        content_type=ContentType.UNKNOWN,
        status=ContentStatus(new_content.status),
        platform=None,
        already_exists=False,
        message="Feed subscription queued" if subscribe_to_feed else "Content queued for analysis",
        task_id=task_id,
        source=new_content.source or SELF_SUBMISSION_SOURCE,
    )
