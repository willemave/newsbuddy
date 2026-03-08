"""Helpers for creating content from instruction-derived links."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.constants import SELF_SUBMISSION_SOURCE
from app.core.logging import get_logger
from app.models.metadata import ContentClassification, ContentStatus, ContentType
from app.models.schema import Content
from app.services.content_analyzer import InstructionLink
from app.services.content_submission import normalize_url
from app.services.gateways.task_queue_gateway import get_task_queue_gateway
from app.services.long_form_images import enqueue_visible_long_form_images_for_content_ids
from app.services.queue import TaskType
from app.services.scraper_configs import ensure_inbox_status

logger = get_logger(__name__)


def _normalize_instruction_links(links: list[InstructionLink], original_url: str) -> list[str]:
    """Normalize and dedupe instruction links.

    Args:
        links: Instruction-provided links.
        original_url: URL of the original content submission.

    Returns:
        List of normalized URLs excluding duplicates and the original URL.
    """
    try:
        original_normalized = normalize_url(original_url)
    except Exception:
        original_normalized = original_url

    seen = {original_normalized}
    normalized_urls: list[str] = []

    for link in links:
        try:
            normalized = normalize_url(str(link.url))
        except Exception:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_urls.append(normalized)

    return normalized_urls


def create_contents_from_instruction_links(
    db: Session,
    source_content: Content,
    links: list[InstructionLink],
    *,
    enqueue_task: Callable[[int], None] | None = None,
) -> list[int]:
    """Create content records for instruction-derived links.

    Args:
        db: Active database session.
        source_content: Content row that produced the instruction links.
        links: Instruction-derived links to turn into content records.
        enqueue_task: Optional enqueue function for ANALYZE_URL tasks.

    Returns:
        List of newly created content IDs.
    """
    if not links:
        return []

    metadata = source_content.content_metadata or {}
    submitter_id = metadata.get("submitted_by_user_id")
    if submitter_id is None:
        logger.info(
            "Skipping instruction link creation (no submitter id)",
            extra={
                "component": "instruction_links",
                "operation": "create_contents",
                "context_data": {"content_id": source_content.id},
            },
        )
        return []

    normalized_urls = _normalize_instruction_links(links, str(source_content.url))
    if not normalized_urls:
        return []

    existing_contents = db.query(Content).filter(Content.url.in_(normalized_urls)).all()
    existing_by_url = {content.url: content for content in existing_contents}

    created_ids: list[int] = []
    existing_inbox_created_ids: list[int] = []
    has_updates = False

    for url in normalized_urls:
        existing = existing_by_url.get(url)
        if existing:
            if ensure_inbox_status(
                db,
                submitter_id,
                existing.id,
                content_type=existing.content_type,
            ):
                existing_inbox_created_ids.append(existing.id)
                has_updates = True
            continue

        new_content = Content(
            url=url,
            source_url=url,
            content_type=ContentType.UNKNOWN.value,
            title=None,
            source=SELF_SUBMISSION_SOURCE,
            platform=None,
            is_aggregate=False,
            status=ContentStatus.NEW.value,
            classification=ContentClassification.TO_READ.value,
            content_metadata={
                "source": SELF_SUBMISSION_SOURCE,
                "submitted_by_user_id": submitter_id,
                "submitted_via": "share_sheet_instruction",
            },
        )

        db.add(new_content)
        db.flush()
        created_ids.append(new_content.id)

        if ensure_inbox_status(
            db,
            submitter_id,
            new_content.id,
            content_type=new_content.content_type,
        ):
            has_updates = True

    if created_ids or has_updates:
        db.commit()
        enqueue_visible_long_form_images_for_content_ids(
            db,
            [*existing_inbox_created_ids, *created_ids],
        )

    if not created_ids:
        return []

    if enqueue_task is None:
        queue_gateway = get_task_queue_gateway()

        def enqueue_task(content_id: int) -> None:
            queue_gateway.enqueue(
                task_type=TaskType.ANALYZE_URL,
                content_id=content_id,
                payload={"content_id": content_id},
            )

    for content_id in created_ids:
        enqueue_task(content_id)

    return created_ids
