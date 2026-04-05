"""Application query for narration payload resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.content_mapper import content_to_domain
from app.repositories.content_detail_repository import get_visible_content
from app.services.voice.persistence import build_summary_narration

NarrationTargetType = Literal["content"]


@dataclass(frozen=True)
class NarrationPayload:
    """Resolved narration payload before formatting or encoding."""

    target_type: NarrationTargetType
    target_id: int
    title: str
    narration_text: str
    audio_filename: str


def execute(
    db: Session,
    *,
    user_id: int,
    target_type: NarrationTargetType,
    target_id: int,
) -> NarrationPayload:
    """Resolve narration payload for one target."""
    if target_type != "content":
        raise HTTPException(status_code=404, detail="Content not found")

    content = get_visible_content(db, user_id=user_id, content_id=target_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Content not found")

    try:
        title = content_to_domain(content).display_title
    except Exception:
        title = (content.title or "").strip() or f"Content {content.id}"

    narration_text = build_summary_narration(content, title=title)
    return NarrationPayload(
        target_type="content",
        target_id=content.id,
        title=title,
        narration_text=narration_text,
        audio_filename=f"content-{content.id}.mp3",
    )
