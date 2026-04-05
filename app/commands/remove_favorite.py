"""Application command for explicit favorite removal."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.schema import Content
from app.repositories import favorites_repository


def execute(db: Session, *, user_id: int, content_id: int) -> dict[str, object]:
    """Remove favorite status for a content item."""
    content = db.query(Content).filter(Content.id == content_id).first()
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    removed = favorites_repository.remove_favorite(db, content_id, user_id)
    return {
        "status": "success" if removed else "not_found",
        "content_id": content_id,
        "message": "Removed from favorites" if removed else "Content was not favorited",
    }
