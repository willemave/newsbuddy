"""Repository for per-user knowledge save operations."""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.db import ContentKnowledgeSave


def save_to_knowledge(db: Session, content_id: int, user_id: int) -> ContentKnowledgeSave:
    """Ensure content is saved to the user's knowledge library."""
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
    db.flush()
    return saved


def remove_from_knowledge(db: Session, content_id: int, user_id: int) -> bool:
    """Remove content from the user's knowledge library."""
    result = db.execute(
        delete(ContentKnowledgeSave).where(
            ContentKnowledgeSave.content_id == content_id,
            ContentKnowledgeSave.user_id == user_id,
        )
    )
    rowcount = getattr(result, "rowcount", 0)
    return bool(rowcount and rowcount > 0)


def list_knowledge_content_ids(db: Session, user_id: int) -> list[int]:
    """Return content ids saved to the user's knowledge library."""
    content_ids = (
        db.execute(
            select(ContentKnowledgeSave.content_id)
            .where(ContentKnowledgeSave.user_id == user_id)
            .distinct()
        )
        .scalars()
        .all()
    )
    return [content_id for content_id in content_ids if content_id is not None]


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
    rowcount = getattr(result, "rowcount", 0)
    return int(rowcount or 0)
