"""Application command for removing content from knowledge."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.content_actions import KnowledgeMutationResponse
from app.models.contracts import KnowledgeMutationStatus
from app.models.db import Content
from app.services import knowledge as knowledge_service


def execute(db: Session, *, user_id: int, content_id: int) -> KnowledgeMutationResponse:
    """Remove a content item from the user's knowledge library."""
    content = db.query(Content).filter(Content.id == content_id).first()
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    try:
        removed = knowledge_service.remove_from_knowledge(db, content_id, user_id)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not remove content from knowledge",
        ) from exc
    return KnowledgeMutationResponse(
        status=KnowledgeMutationStatus.SUCCESS if removed else KnowledgeMutationStatus.NOT_FOUND,
        content_id=content_id,
        is_saved_to_knowledge=False,
        message=("Removed from knowledge" if removed else "Content was not saved to knowledge"),
    )
