"""Repository for content read-status operations."""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.schema import ContentReadStatus

logger = get_logger(__name__)


def _read_status_extra(operation: str, **context_data: Any) -> dict[str, Any]:
    return {
        "component": "read_status",
        "operation": operation,
        "context_data": {key: value for key, value in context_data.items() if value is not None},
    }


def mark_content_as_read(db: Session, content_id: int, user_id: int) -> ContentReadStatus | None:
    """Mark content as read for a user."""
    logger.info(
        "[READ_STATUS] Marking content_id=%s as read for user_id=%s",
        content_id,
        user_id,
        extra=_read_status_extra("mark_content_as_read", content_id=content_id, user_id=user_id),
    )
    try:
        existing = db.execute(
            select(ContentReadStatus).where(
                ContentReadStatus.content_id == content_id,
                ContentReadStatus.user_id == user_id,
            )
        ).scalar_one_or_none()

        if existing:
            existing.read_at = datetime.now(UTC)
            db.commit()
            return existing

        read_status = ContentReadStatus(
            user_id=user_id,
            content_id=content_id,
            read_at=datetime.now(UTC),
        )
        db.add(read_status)
        db.commit()
        db.refresh(read_status)
        return read_status
    except IntegrityError as exc:
        logger.warning(
            "[READ_STATUS] Integrity error while marking read",
            extra=_read_status_extra(
                "mark_content_as_read",
                content_id=content_id,
                user_id=user_id,
                error=str(exc),
            ),
            exc_info=True,
        )
        db.rollback()
        return None
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[READ_STATUS] Unexpected error while marking read",
            extra=_read_status_extra(
                "mark_content_as_read",
                content_id=content_id,
                user_id=user_id,
                error=str(exc),
            ),
        )
        db.rollback()
        return None


def mark_contents_as_read(
    db: Session,
    content_ids: Iterable[int],
    user_id: int,
) -> tuple[int, list[int]]:
    """Mark a batch of content items as read for a user."""
    unique_ids = {content_id for content_id in content_ids if content_id is not None}
    if not unique_ids:
        return 0, []

    timestamp = datetime.now(UTC)
    try:
        existing_records = (
            db.execute(
                select(ContentReadStatus).where(
                    ContentReadStatus.content_id.in_(unique_ids),
                    ContentReadStatus.user_id == user_id,
                )
            )
            .scalars()
            .all()
        )
        existing_ids = {record.content_id for record in existing_records}
        for record in existing_records:
            record.read_at = timestamp

        new_ids = sorted(unique_ids - existing_ids)
        if new_ids:
            db.bulk_save_objects(
                [
                    ContentReadStatus(
                        user_id=user_id,
                        content_id=content_id,
                        read_at=timestamp,
                        created_at=timestamp,
                    )
                    for content_id in new_ids
                ]
            )

        db.commit()
        return len(unique_ids), []
    except IntegrityError as exc:
        logger.warning(
            "[READ_STATUS] Integrity error during bulk mark; retrying individually",
            extra=_read_status_extra("mark_contents_as_read", user_id=user_id, error=str(exc)),
            exc_info=True,
        )
        db.rollback()
        failed_ids: list[int] = []
        marked_count = 0
        for content_id in sorted(unique_ids):
            result = mark_content_as_read(db, content_id, user_id)
            if result is None:
                failed_ids.append(content_id)
                continue
            marked_count += 1
        return marked_count, failed_ids
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[READ_STATUS] Unexpected error during bulk mark",
            extra=_read_status_extra("mark_contents_as_read", user_id=user_id, error=str(exc)),
        )
        db.rollback()
        return 0, sorted(unique_ids)


def get_read_content_ids(db: Session, user_id: int) -> list[int]:
    """Return read content ids for a user."""
    return list(
        db.execute(
            select(ContentReadStatus.content_id)
            .where(ContentReadStatus.user_id == user_id)
            .distinct()
        )
        .scalars()
        .all()
    )


def is_content_read(db: Session, content_id: int, user_id: int) -> bool:
    """Return whether a content item is read by the user."""
    return (
        db.execute(
            select(ContentReadStatus).where(
                ContentReadStatus.content_id == content_id,
                ContentReadStatus.user_id == user_id,
            )
        ).scalar_one_or_none()
        is not None
    )


def mark_content_as_unread(db: Session, content_id: int, user_id: int) -> bool:
    """Remove read status for a single content item."""
    try:
        result = db.execute(
            delete(ContentReadStatus).where(
                ContentReadStatus.content_id == content_id,
                ContentReadStatus.user_id == user_id,
            )
        )
        db.commit()
        return bool(result.rowcount)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[READ_STATUS] Unexpected error while marking unread",
            extra=_read_status_extra(
                "mark_content_as_unread",
                content_id=content_id,
                user_id=user_id,
                error=str(exc),
            ),
        )
        db.rollback()
        return False


def clear_read_status(db: Session, user_id: int) -> int:
    """Clear all read status rows for a user."""
    result = db.execute(delete(ContentReadStatus).where(ContentReadStatus.user_id == user_id))
    db.commit()
    return int(result.rowcount or 0)
