"""Shared rules for long-form generated image eligibility and cleanup."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.orm import Session

from app.models.content_display import is_ready_for_long_form_summary
from app.models.content_mapper import content_to_domain
from app.models.contracts import TaskStatus
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content, ContentStatusEntry, ProcessingTask
from app.services.queue import QueueService, TaskType
from app.utils.image_paths import get_content_images_dir

CANCELLED_NOT_VISIBLE_UNDER_FEED_RULES = "cancelled_not_visible_under_feed_rules"
LONG_FORM_IMAGE_CONTENT_TYPES = {
    ContentType.ARTICLE.value,
    ContentType.PODCAST.value,
}


def _require_content_id(content: Content) -> int:
    content_id = content.id
    if content_id is None:
        raise ValueError("Content must be persisted before use")
    return content_id


def _require_task_id(task: ProcessingTask) -> int:
    task_id = task.id
    if task_id is None:
        raise ValueError("Processing task must be persisted before use")
    return task_id


class QueueEnqueuer(Protocol):
    """Protocol for queue services used in tests and production."""

    def enqueue(
        self,
        task_type: TaskType,
        content_id: int | None = None,
        payload: dict | None = None,
        queue_name=None,
        dedupe: bool | None = None,
    ) -> int:
        """Enqueue a task and return its task id."""


def is_long_form_image_content_type(content_type: str | None) -> bool:
    """Return True when a content type can have generated long-form images."""
    return bool(content_type and content_type in LONG_FORM_IMAGE_CONTENT_TYPES)


def has_summary_for_generated_image(content: Content) -> bool:
    """Return True when content has summary data suitable for image prompts."""
    metadata = content.content_metadata or {}
    return bool(metadata.get("summary"))


def has_generated_long_form_image(content: Content) -> bool:
    """Return True when content already has a generated image asset."""
    metadata = content.content_metadata or {}
    if metadata.get("image_generated_at"):
        return True
    image_path = get_content_images_dir() / f"{content.id}.png"
    return image_path.exists()


def has_active_generate_image_task(db: Session, content_id: int) -> bool:
    """Return True when a content item already has an in-flight image task."""
    return bool(
        db.query(ProcessingTask.id)
        .filter(ProcessingTask.content_id == content_id)
        .filter(ProcessingTask.task_type == TaskType.GENERATE_IMAGE.value)
        .filter(ProcessingTask.status.in_([TaskStatus.PENDING.value, TaskStatus.PROCESSING.value]))
        .first()
    )


def is_visible_in_any_long_form_inbox(db: Session, content_id: int) -> bool:
    """Return True when content appears in at least one user's inbox."""
    return bool(
        db.query(ContentStatusEntry.id)
        .filter(ContentStatusEntry.content_id == content_id)
        .filter(ContentStatusEntry.status == "inbox")
        .first()
    )


def is_visible_long_form_image_candidate(db: Session, content: Content) -> bool:
    """Return True when content should participate in generated image flows."""
    if not is_long_form_image_content_type(content.content_type):
        return False
    if content.status != ContentStatus.COMPLETED.value:
        return False
    if content.classification == "skip":
        return False
    if not is_visible_in_any_long_form_inbox(db, _require_content_id(content)):
        return False
    if not has_summary_for_generated_image(content):
        return False
    if content.content_type == ContentType.ARTICLE.value:
        try:
            domain_content = content_to_domain(content)
        except Exception:  # noqa: BLE001
            return False
        if not is_ready_for_long_form_summary(domain_content):
            return False
    return True


def enqueue_visible_long_form_image_if_needed(
    db: Session,
    content: Content,
    *,
    queue_service: QueueEnqueuer | None = None,
) -> int | None:
    """Enqueue image generation for a visible long-form item when needed."""
    if not is_visible_long_form_image_candidate(db, content):
        return None
    if has_generated_long_form_image(content):
        return None
    if has_active_generate_image_task(db, _require_content_id(content)):
        return None

    effective_queue_service = queue_service or QueueService()
    return effective_queue_service.enqueue(
        task_type=TaskType.GENERATE_IMAGE,
        content_id=_require_content_id(content),
    )


def enqueue_visible_long_form_images_for_content_ids(
    db: Session,
    content_ids: list[int],
    *,
    queue_service: QueueEnqueuer | None = None,
) -> list[int]:
    """Enqueue generated images for eligible content ids."""
    if not content_ids:
        return []

    effective_queue_service = queue_service or QueueService()
    enqueued_task_ids: list[int] = []
    unique_content_ids = list(dict.fromkeys(content_ids))
    contents = db.query(Content).filter(Content.id.in_(unique_content_ids)).all()
    for content in contents:
        task_id = enqueue_visible_long_form_image_if_needed(
            db,
            content,
            queue_service=effective_queue_service,
        )
        if task_id is not None:
            enqueued_task_ids.append(task_id)
    return enqueued_task_ids


def cancel_ineligible_pending_generate_image_tasks(
    db: Session,
    *,
    limit: int | None = None,
) -> list[int]:
    """Cancel pending image tasks for content outside visible feed rules."""
    task_ids = list_ineligible_pending_generate_image_task_ids(db, limit=limit)
    if not task_ids:
        return []

    tasks = db.query(ProcessingTask).filter(ProcessingTask.id.in_(task_ids)).all()
    completed_at = datetime.now(UTC).replace(tzinfo=None)
    for task in tasks:
        task.status = TaskStatus.FAILED.value
        task.error_message = CANCELLED_NOT_VISIBLE_UNDER_FEED_RULES
        task.completed_at = completed_at

    db.commit()
    return task_ids


def list_ineligible_pending_generate_image_task_ids(
    db: Session,
    *,
    limit: int | None = None,
) -> list[int]:
    """Return pending generate-image task ids outside visible feed rules."""
    query = (
        db.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.GENERATE_IMAGE.value)
        .filter(ProcessingTask.status == TaskStatus.PENDING.value)
        .order_by(ProcessingTask.created_at.asc(), ProcessingTask.id.asc())
    )
    if limit is not None:
        query = query.limit(limit)

    tasks = query.all()
    canceled_ids: list[int] = []

    for task in tasks:
        if task.content_id is None:
            canceled_ids.append(_require_task_id(task))
            continue

        content = db.query(Content).filter(Content.id == task.content_id).first()
        if content and is_visible_long_form_image_candidate(db, content):
            continue

        canceled_ids.append(_require_task_id(task))

    return canceled_ids
