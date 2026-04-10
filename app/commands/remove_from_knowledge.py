"""Application command for removing content from knowledge."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.schema import Content
from app.repositories import knowledge_repository


def execute(db: Session, *, user_id: int, content_id: int) -> dict[str, object]:
    """Remove a content item from the user's knowledge library."""
    content = db.query(Content).filter(Content.id == content_id).first()
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    removed = knowledge_repository.remove_from_knowledge(db, content_id, user_id)
    return {
        "status": "success" if removed else "not_found",
        "content_id": content_id,
        "is_saved_to_knowledge": False,
        "message": (
            "Removed from knowledge" if removed else "Content was not saved to knowledge"
        ),
    }
