"""Application command for saving content to knowledge."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.db import Content
from app.services import knowledge as knowledge_service


def execute(db: Session, *, user_id: int, content_id: int) -> dict[str, object]:
    """Ensure a content item is saved to the user's knowledge library."""
    content = db.query(Content).filter(Content.id == content_id).first()
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    try:
        knowledge_service.save_to_knowledge(db, content_id, user_id)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not save content to knowledge",
        ) from exc

    return {
        "status": "success",
        "content_id": content_id,
        "is_saved_to_knowledge": True,
        "message": "Saved to knowledge",
    }
