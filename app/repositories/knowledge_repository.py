"""Repository for per-user knowledge save operations."""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.schema import ContentKnowledgeSave
from app.services.personal_markdown_library import sync_personal_markdown_for_content

logger = get_logger(__name__)


def _sync_personal_markdown_after_knowledge_mutation(
    db: Session,
    *,
    user_id: int,
    content_id: int,
) -> None:
    try:
        sync_personal_markdown_for_content(db, user_id=user_id, content_id=content_id)
    except Exception:
        logger.exception(
            "Failed to sync personal markdown for content_id=%s, user_id=%s",
            content_id,
            user_id,
        )


def toggle_knowledge_save(
    db: Session,
    content_id: int,
    user_id: int,
) -> tuple[bool, ContentKnowledgeSave | None]:
    """Toggle whether content belongs in the user's knowledge library."""
    logger.debug("Toggling knowledge save for content_id=%s, user_id=%s", content_id, user_id)
    try:
        existing = db.execute(
            select(ContentKnowledgeSave).where(
                ContentKnowledgeSave.content_id == content_id,
                ContentKnowledgeSave.user_id == user_id,
            )
        ).scalar_one_or_none()

        if existing:
            db.delete(existing)
            db.commit()
            _sync_personal_markdown_after_knowledge_mutation(
                db,
                user_id=user_id,
                content_id=content_id,
            )
            return (False, None)

        saved = ContentKnowledgeSave(
            user_id=user_id,
            content_id=content_id,
            saved_at=datetime.now(UTC),
        )
        db.add(saved)
        db.commit()
        db.refresh(saved)
        _sync_personal_markdown_after_knowledge_mutation(
            db,
            user_id=user_id,
            content_id=content_id,
        )
        return (True, saved)
    except IntegrityError:
        logger.exception(
            "Integrity error toggling knowledge save for content_id=%s, user_id=%s",
            content_id,
            user_id,
        )
        db.rollback()
        return (False, None)
    except Exception:
        logger.exception(
            "Unexpected error toggling knowledge save for content_id=%s, user_id=%s",
            content_id,
            user_id,
        )
        db.rollback()
        return (False, None)


def save_to_knowledge(db: Session, content_id: int, user_id: int) -> ContentKnowledgeSave | None:
    """Ensure content is saved to the user's knowledge library."""
    try:
        existing = db.execute(
            select(ContentKnowledgeSave).where(
                ContentKnowledgeSave.content_id == content_id,
                ContentKnowledgeSave.user_id == user_id,
            )
        ).scalar_one_or_none()
        if existing:
            return existing

        saved = ContentKnowledgeSave(
            user_id=user_id,
            content_id=content_id,
            saved_at=datetime.now(UTC),
        )
        db.add(saved)
        db.commit()
        db.refresh(saved)
        _sync_personal_markdown_after_knowledge_mutation(
            db,
            user_id=user_id,
            content_id=content_id,
        )
        return saved
    except Exception:
        logger.exception(
            "Error saving content_id=%s to knowledge for user_id=%s",
            content_id,
            user_id,
        )
        db.rollback()
        return None


def remove_from_knowledge(db: Session, content_id: int, user_id: int) -> bool:
    """Remove content from the user's knowledge library."""
    try:
        result = db.execute(
            delete(ContentKnowledgeSave).where(
                ContentKnowledgeSave.content_id == content_id,
                ContentKnowledgeSave.user_id == user_id,
            )
        )
        db.commit()
        _sync_personal_markdown_after_knowledge_mutation(
            db,
            user_id=user_id,
            content_id=content_id,
        )
        return result.rowcount > 0
    except Exception:
        logger.exception(
            "Error removing content_id=%s from knowledge for user_id=%s",
            content_id,
            user_id,
        )
        db.rollback()
        return False


def list_knowledge_content_ids(db: Session, user_id: int) -> list[int]:
    """Return content ids saved to the user's knowledge library."""
    return list(
        db.execute(
            select(ContentKnowledgeSave.content_id)
            .where(ContentKnowledgeSave.user_id == user_id)
            .distinct()
        )
        .scalars()
        .all()
    )


def is_saved_to_knowledge(db: Session, content_id: int, user_id: int) -> bool:
    """Return whether content is saved to the user's knowledge library."""
    return (
        db.execute(
            select(ContentKnowledgeSave).where(
                ContentKnowledgeSave.content_id == content_id,
                ContentKnowledgeSave.user_id == user_id,
            )
        ).scalar_one_or_none()
        is not None
    )


def clear_knowledge_library(db: Session, user_id: int) -> int:
    """Clear all knowledge-saved content for a user."""
    result = db.execute(delete(ContentKnowledgeSave).where(ContentKnowledgeSave.user_id == user_id))
    db.commit()
    return int(result.rowcount or 0)
