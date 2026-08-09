"""Helpers for user-submitted one-off content."""

from __future__ import annotations

from pydantic import HttpUrl, TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import SELF_SUBMISSION_SOURCE
from app.core.logging import get_logger
from app.models.api.submissions import ContentSubmissionResponse, SubmitContentRequest
from app.models.contracts import ContentClassification, ContentStatus, ContentType
from app.models.db import Content, ProcessingTask
from app.models.db.users import User
from app.models.metadata.access import metadata_view
from app.models.metadata.state import (
    append_share_and_chat_request,
    normalize_metadata_shape,
    update_processing_state,
)
from app.repositories import read_status_repository
from app.services import knowledge as knowledge_service
from app.services.dig_deeper import enqueue_dig_deeper_task
from app.services.gateways.task_queue_gateway import get_task_queue_gateway
from app.services.long_form_images import build_visible_long_form_image_task_requests
from app.services.queue import TaskEnqueueRequest, TaskStatus, TaskType
from app.services.scraper_configs import ensure_inbox_status

logger = get_logger(__name__)

URL_ADAPTER = TypeAdapter(HttpUrl)
REANALYZE_EXISTING_STATUSES: set[str] = {
    ContentStatus.NEW.value,
    ContentStatus.PENDING.value,
    ContentStatus.FAILED.value,
    ContentStatus.SKIPPED.value,
}
X_BOOKMARK_SUBMISSION_CHANNEL = "x_bookmarks"


def normalize_url(raw_url: str) -> str:
    """Normalize and validate the incoming URL string.

    Args:
        raw_url: URL provided by the client.

    Returns:
        Validated and normalized URL string.
    """
    return str(URL_ADAPTER.validate_python(raw_url)).strip()


def _require_user_id(user: User) -> int:
    user_id = user.id
    if user_id is None:
        raise ValueError("User is missing an id")
    return int(user_id)


def _require_content_id(content: Content) -> int:
    content_id = content.id
    if content_id is None:
        raise ValueError("Content is missing an id")
    return int(content_id)


def _require_content_type(content: Content) -> str:
    content_type = content.content_type
    if not isinstance(content_type, str) or not content_type:
        raise ValueError("Content is missing a content_type")
    return content_type


def _require_content_status(content: Content) -> str:
    status = content.status
    if not isinstance(status, str) or not status:
        raise ValueError("Content is missing a status")
    return status


def _ensure_analyze_url_task(
    db: Session,
    content_id: int,
    user_id: int,
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
    active_statuses = [TaskStatus.PENDING.value, TaskStatus.PROCESSING.value]
    existing_task = (
        db.query(ProcessingTask)
        .filter(
            ProcessingTask.content_id == content_id,
            ProcessingTask.task_type == TaskType.ANALYZE_URL.value,
            ProcessingTask.status.in_(active_statuses),
        )
        .first()
    )
    if existing_task:
        existing_payload = dict(existing_task.payload or {})
        existing_payload.setdefault("content_id", content_id)
        updated = False
        cleaned_instruction = instruction.strip() if instruction else None
        if cleaned_instruction and existing_payload.get("instruction") != cleaned_instruction:
            existing_payload["instruction"] = cleaned_instruction
            updated = True
        if crawl_links and existing_payload.get("crawl_links") is not True:
            existing_payload["crawl_links"] = True
            updated = True
        if subscribe_to_feed and existing_payload.get("subscribe_to_feed") is not True:
            existing_payload["subscribe_to_feed"] = True
            updated = True
        if updated:
            existing_task.payload = existing_payload
        existing_task_id = existing_task.id
        if existing_task_id is None:
            raise ValueError("Existing analyze task is missing an id")
        resolved_task_id = int(existing_task_id)
        get_task_queue_gateway().grant_access_in_session(
            db,
            task_id=resolved_task_id,
            user_id=user_id,
        )
        return resolved_task_id

    # Ordinary repeats can observe the existing processing job. Action-bearing
    # submissions need a fresh ANALYZE_URL turn because PROCESS_CONTENT cannot
    # perform feed discovery, instruction analysis, or link crawling.
    cleaned_instruction = instruction.strip() if instruction else None
    if not (cleaned_instruction or crawl_links or subscribe_to_feed):
        existing_process_task = (
            db.query(ProcessingTask)
            .filter(
                ProcessingTask.content_id == content_id,
                ProcessingTask.task_type == TaskType.PROCESS_CONTENT.value,
                ProcessingTask.status.in_(active_statuses),
            )
            .first()
        )
        if existing_process_task is not None:
            existing_task_id = existing_process_task.id
            if existing_task_id is None:
                raise ValueError("Existing process task is missing an id")
            resolved_task_id = int(existing_task_id)
            get_task_queue_gateway().grant_access_in_session(
                db,
                task_id=resolved_task_id,
                user_id=user_id,
            )
            return resolved_task_id

    payload: dict[str, object] = {"content_id": content_id}
    if cleaned_instruction:
        payload["instruction"] = cleaned_instruction
    if crawl_links:
        payload["crawl_links"] = True
    if subscribe_to_feed:
        payload["subscribe_to_feed"] = True

    return get_task_queue_gateway().enqueue_many_in_session(
        db,
        [
            TaskEnqueueRequest(
                TaskType.ANALYZE_URL,
                content_id=content_id,
                payload=payload,
                dedupe=True,
                access_user_id=user_id,
            )
        ],
    )[0]


def _apply_submission_user_state(
    db: Session,
    *,
    user_id: int,
    content_id: int,
    save_to_knowledge_and_mark_read: bool,
    share_and_chat: bool,
    chat_initial_message: str | None,
    enqueue_dig_deeper: bool = False,
) -> None:
    should_mark_read = save_to_knowledge_and_mark_read or share_and_chat
    if should_mark_read:
        read_status_repository.mark_content_as_read_in_session(db, content_id, user_id)
    if save_to_knowledge_and_mark_read:
        knowledge_service.save_to_knowledge_in_session(db, content_id, user_id)
    if share_and_chat and enqueue_dig_deeper:
        enqueue_dig_deeper_task(
            db,
            content_id,
            user_id,
            initial_message=chat_initial_message,
        )


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


def _should_create_inbox_status(*, submitted_via: str, subscribe_to_feed: bool) -> bool:
    if subscribe_to_feed:
        return False
    return submitted_via != X_BOOKMARK_SUBMISSION_CHANNEL


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
    chat_initial_message = (
        payload.chat_initial_message.strip() if payload.chat_initial_message else None
    )
    save_to_knowledge_and_mark_read = (
        payload.save_to_knowledge_and_mark_read and not subscribe_to_feed
    )
    create_inbox_status = _should_create_inbox_status(
        submitted_via=submission_channel,
        subscribe_to_feed=subscribe_to_feed,
    )
    platform_hint = (payload.platform or "").strip().lower() or None
    current_user_id = _require_user_id(current_user)

    existing = db.query(Content).filter(Content.url == normalized_url).first()
    if existing:
        existing_content_id = _require_content_id(existing)
        existing_content_type = _require_content_type(existing)
        existing_status = _require_content_status(existing)
        if not existing.source_url:
            existing.source_url = raw_url
        if payload.title and not existing.title:
            existing.title = payload.title
        if platform_hint and not existing.platform:
            existing.platform = platform_hint
        if subscribe_to_feed:
            existing_metadata = normalize_metadata_shape(dict(existing.content_metadata or {}))
            existing_metadata = update_processing_state(
                existing_metadata,
                subscribe_to_feed=True,
            )
            if metadata_view(existing_metadata).submission_user_id() is None:
                existing_metadata = update_processing_state(
                    existing_metadata,
                    submitted_by_user_id=current_user_id,
                )
            if not metadata_view(existing_metadata).processing_flag("submitted_via"):
                existing_metadata = update_processing_state(
                    existing_metadata,
                    submitted_via=submission_channel,
                )
            if platform_hint and not metadata_view(existing_metadata).processing_flag(
                "platform_hint"
            ):
                existing_metadata = update_processing_state(
                    existing_metadata,
                    platform_hint=platform_hint,
                )
            existing.content_metadata = existing_metadata
        else:
            if share_and_chat and existing_status != ContentStatus.COMPLETED.value:
                existing.content_metadata = append_share_and_chat_request(
                    existing.content_metadata,
                    user_id=current_user_id,
                    initial_message=chat_initial_message,
                )
            if create_inbox_status:
                ensure_inbox_status(
                    db,
                    current_user_id,
                    existing_content_id,
                    content_type=existing_content_type,
                )
            _apply_submission_user_state(
                db,
                user_id=current_user_id,
                content_id=existing_content_id,
                save_to_knowledge_and_mark_read=save_to_knowledge_and_mark_read,
                share_and_chat=share_and_chat,
                chat_initial_message=chat_initial_message,
                enqueue_dig_deeper=existing_status == ContentStatus.COMPLETED.value,
            )
        task_id: int | None = None
        if _should_enqueue_analysis_for_existing_content(
            existing_status=existing_status,
            instruction=instruction,
            crawl_links=crawl_links,
            subscribe_to_feed=subscribe_to_feed,
        ):
            task_id = _ensure_analyze_url_task(
                db,
                existing_content_id,
                current_user_id,
                instruction=instruction,
                crawl_links=crawl_links,
                subscribe_to_feed=subscribe_to_feed,
            )
        image_requests = build_visible_long_form_image_task_requests(
            db,
            [existing_content_id],
        )
        if image_requests:
            get_task_queue_gateway().enqueue_many_in_session(db, image_requests)
        db.commit()
        db.refresh(existing)
        if save_to_knowledge_and_mark_read:
            knowledge_service.sync_knowledge_markdown(
                db,
                content_id=existing_content_id,
                user_id=current_user_id,
            )
        return ContentSubmissionResponse(
            content_id=existing_content_id,
            content_type=ContentType(existing_content_type),
            status=ContentStatus(existing_status),
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
        submitted_by_user_id=current_user_id,
        submitted_via=submission_channel,
    )
    if subscribe_to_feed:
        metadata = update_processing_state(metadata, subscribe_to_feed=True)
    if platform_hint:
        metadata = update_processing_state(metadata, platform_hint=platform_hint)
    if share_and_chat:
        metadata = append_share_and_chat_request(
            metadata,
            user_id=current_user_id,
            initial_message=chat_initial_message,
        )

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
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        logger.warning("Self-submission hit duplicate constraint for %s: %s", normalized_url, exc)
        existing = db.query(Content).filter(Content.url == normalized_url).first()
        if not existing:
            raise
        # Re-enter the existing-row path so the race loser receives the same
        # inbox, Knowledge, read, chat, metadata, and task finalization as a
        # request that observed the winner before attempting its insert.
        return submit_user_content(
            db,
            payload,
            current_user,
            submitted_via=submission_channel,
        )

    new_content_id = _require_content_id(new_content)
    new_content_type = _require_content_type(new_content)
    new_content_status = _require_content_status(new_content)
    if not subscribe_to_feed:
        if create_inbox_status:
            ensure_inbox_status(db, current_user_id, new_content_id, content_type=new_content_type)
        _apply_submission_user_state(
            db,
            user_id=current_user_id,
            content_id=new_content_id,
            save_to_knowledge_and_mark_read=save_to_knowledge_and_mark_read,
            share_and_chat=share_and_chat,
            chat_initial_message=chat_initial_message,
        )
    task_id = _ensure_analyze_url_task(
        db,
        new_content_id,
        current_user_id,
        instruction=instruction,
        crawl_links=crawl_links,
        subscribe_to_feed=subscribe_to_feed,
    )
    image_requests = build_visible_long_form_image_task_requests(db, [new_content_id])
    if image_requests:
        get_task_queue_gateway().enqueue_many_in_session(db, image_requests)
    db.commit()
    db.refresh(new_content)
    if save_to_knowledge_and_mark_read:
        knowledge_service.sync_knowledge_markdown(
            db,
            content_id=new_content_id,
            user_id=current_user_id,
        )

    return ContentSubmissionResponse(
        content_id=new_content_id,
        content_type=ContentType.UNKNOWN,
        status=ContentStatus(new_content_status),
        platform=None,
        already_exists=False,
        message="Feed subscription queued" if subscribe_to_feed else "Content queued for analysis",
        task_id=task_id,
        source=new_content.source or SELF_SUBMISSION_SOURCE,
    )
