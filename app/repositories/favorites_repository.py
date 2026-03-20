"""Repository for content favorites operations."""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.schema import ContentFavorites

logger = get_logger(__name__)


def toggle_favorite(
    db: Session, content_id: int, user_id: int
) -> tuple[bool, ContentFavorites | None]:
    """Toggle favorite status for content."""
    logger.debug("Toggling favorite for content_id=%s, user_id=%s", content_id, user_id)
    try:
        existing = db.execute(
            select(ContentFavorites).where(
                ContentFavorites.content_id == content_id,
                ContentFavorites.user_id == user_id,
            )
        ).scalar_one_or_none()

        if existing:
            db.delete(existing)
            db.commit()
            return (False, None)

        favorite = ContentFavorites(
            user_id=user_id,
            content_id=content_id,
            favorited_at=datetime.now(UTC),
        )
        db.add(favorite)
        db.commit()
        db.refresh(favorite)
        return (True, favorite)
    except IntegrityError:
        logger.exception(
            "Integrity error toggling favorite for content_id=%s, user_id=%s",
            content_id,
            user_id,
        )
        db.rollback()
        return (False, None)
    except Exception:
        logger.exception(
            "Unexpected error toggling favorite for content_id=%s, user_id=%s",
            content_id,
            user_id,
        )
        db.rollback()
        return (False, None)


def add_favorite(db: Session, content_id: int, user_id: int) -> ContentFavorites | None:
    """Add content to favorites."""
    try:
        existing = db.execute(
            select(ContentFavorites).where(
                ContentFavorites.content_id == content_id,
                ContentFavorites.user_id == user_id,
            )
        ).scalar_one_or_none()
        if existing:
            return existing

        favorite = ContentFavorites(
            user_id=user_id,
            content_id=content_id,
            favorited_at=datetime.now(UTC),
        )
        db.add(favorite)
        db.commit()
        db.refresh(favorite)
        return favorite
    except Exception:
        logger.exception(
            "Error adding content_id=%s to favorites for user_id=%s",
            content_id,
            user_id,
        )
        db.rollback()
        return None


def remove_favorite(db: Session, content_id: int, user_id: int) -> bool:
    """Remove content from favorites."""
    try:
        result = db.execute(
            delete(ContentFavorites).where(
                ContentFavorites.content_id == content_id,
                ContentFavorites.user_id == user_id,
            )
        )
        db.commit()
        return result.rowcount > 0
    except Exception:
        logger.exception(
            "Error removing content_id=%s from favorites for user_id=%s",
            content_id,
            user_id,
        )
        db.rollback()
        return False


def get_favorite_content_ids(db: Session, user_id: int) -> list[int]:
    """Return favorited content ids for a user."""
    return list(
        db.execute(
            select(ContentFavorites.content_id)
            .where(ContentFavorites.user_id == user_id)
            .distinct()
        )
        .scalars()
        .all()
    )


def is_content_favorited(db: Session, content_id: int, user_id: int) -> bool:
    """Return whether a content item is favorited by the user."""
    return (
        db.execute(
            select(ContentFavorites).where(
                ContentFavorites.content_id == content_id,
                ContentFavorites.user_id == user_id,
            )
        ).scalar_one_or_none()
        is not None
    )


def clear_favorites(db: Session, user_id: int) -> int:
    """Clear all favorites for a user."""
    result = db.execute(delete(ContentFavorites).where(ContentFavorites.user_id == user_id))
    db.commit()
    return int(result.rowcount or 0)
