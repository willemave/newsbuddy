"""Application query for canonical content body access."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.content import ContentBodyResponse
from app.repositories.content_detail_repository import get_visible_content
from app.services.content_bodies import ContentBodyVariant, get_content_body_resolver

MAX_CONTENT_BODY_RESPONSE_CHARS = 32_000
TRUNCATED_BODY_NOTICE = (
    "\n\n[Content truncated for app rendering. Open the original source for the full text.]"
)


def _truncate_body_text(text: str) -> str:
    """Bound oversized body responses for the current mobile renderer."""
    if len(text) <= MAX_CONTENT_BODY_RESPONSE_CHARS:
        return text

    available = MAX_CONTENT_BODY_RESPONSE_CHARS - len(TRUNCATED_BODY_NOTICE)
    if available <= 0:
        return TRUNCATED_BODY_NOTICE.strip()

    trimmed = text[:available].rstrip()
    split_at = max(trimmed.rfind("\n\n"), trimmed.rfind("\n"), trimmed.rfind(" "))
    if split_at >= available // 2:
        trimmed = trimmed[:split_at].rstrip()

    return f"{trimmed}{TRUNCATED_BODY_NOTICE}"


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
        text=_truncate_body_text(resolved.text),
        updated_at=resolved.updated_at.isoformat() if resolved.updated_at else None,
    )
