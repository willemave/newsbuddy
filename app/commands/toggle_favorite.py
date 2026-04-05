"""Application command for favorite toggling."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.schema import Content
from app.repositories import favorites_repository


def execute(db: Session, *, user_id: int, content_id: int) -> dict[str, object]:
    """Toggle favorite status for a content item."""
    content = db.query(Content).filter(Content.id == content_id).first()
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    is_favorited, _ = favorites_repository.toggle_favorite(db, content_id, user_id)
    return {
        "status": "success",
        "content_id": content_id,
        "is_favorited": is_favorited,
    }
