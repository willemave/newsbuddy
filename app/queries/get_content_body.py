"""Application query for canonical content body access."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.common import ContentBodyResponse
from app.repositories.content_detail_repository import get_visible_content
from app.services.content_bodies import ContentBodyVariant, get_content_body_resolver


def execute(
    db: Session,
    *,
    user_id: int,
    content_id: int,
    variant: str,
) -> ContentBodyResponse:
    """Return canonical body text for a visible content item."""
    content = get_visible_content(db, user_id=user_id, content_id=content_id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    resolved = get_content_body_resolver().resolve(
        db,
        content=content,
        variant=ContentBodyVariant(variant),
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="Content body not found")

    return ContentBodyResponse(
        content_id=resolved.content_id,
        variant=resolved.variant.value,
        kind=resolved.kind,
        format=resolved.format.value,
        text=resolved.text,
        updated_at=resolved.updated_at.isoformat() if resolved.updated_at else None,
    )
