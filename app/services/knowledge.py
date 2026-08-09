"""Knowledge-library mutation orchestration."""

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.db import ContentKnowledgeSave
from app.repositories import knowledge_repository
from app.services.personal_markdown_library import sync_personal_markdown_for_content

logger = get_logger(__name__)

list_knowledge_content_ids = knowledge_repository.list_knowledge_content_ids
is_saved_to_knowledge = knowledge_repository.is_saved_to_knowledge


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
        db.rollback()


def save_to_knowledge(db: Session, content_id: int, user_id: int) -> ContentKnowledgeSave:
    """Save content to knowledge, committing the row and syncing markdown best-effort."""
    saved = save_to_knowledge_in_session(db, content_id, user_id)
    db.commit()
    db.refresh(saved)
    _sync_personal_markdown_after_knowledge_mutation(
        db,
        user_id=user_id,
        content_id=content_id,
    )
    return saved


def save_to_knowledge_in_session(
    db: Session,
    content_id: int,
    user_id: int,
) -> ContentKnowledgeSave:
    """Save content to Knowledge without owning the caller's transaction."""

    return knowledge_repository.save_to_knowledge(db, content_id, user_id)


def sync_knowledge_markdown(db: Session, *, content_id: int, user_id: int) -> None:
    """Run the best-effort markdown projection after a committed Knowledge save."""

    _sync_personal_markdown_after_knowledge_mutation(
        db,
        user_id=user_id,
        content_id=content_id,
    )


def remove_from_knowledge(db: Session, content_id: int, user_id: int) -> bool:
    """Remove content from knowledge, committing the row and syncing markdown best-effort."""
    removed = remove_from_knowledge_in_session(db, content_id, user_id)
    db.commit()
    _sync_personal_markdown_after_knowledge_mutation(
        db,
        user_id=user_id,
        content_id=content_id,
    )
    return removed


def remove_from_knowledge_in_session(db: Session, content_id: int, user_id: int) -> bool:
    """Remove content from Knowledge without owning the caller's transaction."""

    return knowledge_repository.remove_from_knowledge(db, content_id, user_id)


def clear_knowledge_library(db: Session, user_id: int) -> int:
    """Clear all saved content for a user and commit the mutation."""
    count = knowledge_repository.clear_knowledge_library(db, user_id)
    db.commit()
    return count
