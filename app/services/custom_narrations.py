"""Custom narration source collection and prompt shaping."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.api.audio_episodes import CUSTOM_NARRATION_MAX_CONTENT_IDS
from app.models.contracts import ContentType
from app.models.db import Content
from app.repositories.content_repository import build_visibility_context
from app.services.audio_episode_sources import (
    LONGFORM_BODY_MAX_CHARS,
    build_content_source_payload,
)
from app.services.content_bodies import get_content_body_resolver

CUSTOM_NARRATION_KIND: Literal["custom_narration"] = "custom_narration"
CUSTOM_NARRATION_MAX_SOURCES = CUSTOM_NARRATION_MAX_CONTENT_IDS
CUSTOM_NARRATION_DIALOGUE_TEXT_CHAR_LIMIT = 4_500
CUSTOM_NARRATION_SOURCE_TOTAL_CHAR_LIMIT = 24_000
CUSTOM_NARRATION_SOURCE_MIN_CHARS = 2_000


def build_custom_narration_source_snapshot(
    db: Session,
    *,
    user_id: int,
    content_ids: list[int],
) -> dict[str, Any]:
    """Build a bounded source snapshot for selected articles and podcasts."""

    normalized_content_ids = _normalize_custom_narration_content_ids(content_ids)
    source_text_budget = _source_text_budget(len(normalized_content_ids))
    body_resolver = get_content_body_resolver()
    source_items: list[dict[str, Any]] = []
    for content_id in normalized_content_ids:
        content = _get_visible_or_saved_content(db, user_id=user_id, content_id=content_id)
        if content is None:
            raise HTTPException(status_code=404, detail=f"Content {content_id} not found")

        content_type = str(content.content_type or "")
        if content_type not in {ContentType.ARTICLE.value, ContentType.PODCAST.value}:
            raise HTTPException(
                status_code=400,
                detail="Custom narrations only support articles and podcasts",
            )

        body_text = body_resolver.resolve_text(db, content=content)
        if not body_text:
            raise HTTPException(
                status_code=400,
                detail=f"No article or transcript text is available for content {content_id}",
            )

        source_items.append(
            build_content_source_payload(
                content,
                body_text=body_text,
                source_text_max_chars=source_text_budget,
            )
        )

    return {
        "kind": CUSTOM_NARRATION_KIND,
        "source_count": len(source_items),
        "content_ids": normalized_content_ids,
        "source_text_budget_chars": source_text_budget,
        "source_text_total_chars": sum(
            int(item.get("source_text_chars") or 0) for item in source_items
        ),
        "source_text_included_chars": sum(
            int(item.get("source_text_included_chars") or 0) for item in source_items
        ),
        "items": source_items,
    }


def _normalize_custom_narration_content_ids(content_ids: list[int]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw_content_id in content_ids:
        try:
            content_id = int(raw_content_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Content ids must be integers") from None
        if content_id <= 0:
            raise HTTPException(status_code=400, detail="Content ids must be positive")
        if content_id in seen:
            continue
        seen.add(content_id)
        normalized.append(content_id)
    if not normalized:
        raise HTTPException(status_code=400, detail="Select at least one article or podcast")
    if len(normalized) > CUSTOM_NARRATION_MAX_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Select at most {CUSTOM_NARRATION_MAX_SOURCES} sources",
        )
    return normalized


def _get_visible_or_saved_content(
    db: Session,
    *,
    user_id: int,
    content_id: int,
) -> Content | None:
    """Return completed content visible from Long Read or saved Knowledge."""

    context = build_visibility_context(user_id)
    return (
        db.query(Content)
        .filter(
            Content.id == content_id,
            Content.status == "completed",
            or_(context.is_in_inbox, context.is_saved_to_knowledge),
            (Content.classification != "skip") | (Content.classification.is_(None)),
        )
        .first()
    )


def custom_narration_title(source_snapshot: dict[str, Any], *, title: str | None) -> str:
    normalized_title = (title or "").strip()
    if normalized_title:
        return normalized_title
    source_items = source_snapshot.get("items")
    if not isinstance(source_items, list) or not source_items:
        return "Custom narration"
    first_item = source_items[0] if isinstance(source_items[0], dict) else {}
    first_title = str(first_item.get("title") or "Selected sources").strip()
    if len(source_items) == 1:
        return f"Narration: {first_title}"
    return f"Narration: {first_title} + {len(source_items) - 1} more"


def build_custom_narration_prompt(source_snapshot: dict[str, Any]) -> str:
    return f"""Create one cohesive podcast-style narration from the selected articles and
podcast transcripts.

Goal:
- Synthesize across all selected sources as one episode, not separate mini-summaries.
- Use the supplied source excerpts plus summaries. Each source is budgeted to preserve coverage.
- Explain the shared themes, contradictions, evidence, and implications.
- Preserve important source-specific details when they materially support the synthesis.
- Keep the discussion grounded: if a point is not in the selected sources, do not include it.

Shape:
- 500-700 spoken words.
- Hard cap: {CUSTOM_NARRATION_DIALOGUE_TEXT_CHAR_LIMIT} characters across all spoken turn text.
- 10-14 turns.
- Use speaker='host' for setup and transitions, speaker='cohost' for synthesis, and
  speaker='expert' for sharper analysis.
- Start by framing why these sources belong together.
- End with a concise takeaway and what the listener should remember.

Selected source JSON:
{json.dumps(source_snapshot, ensure_ascii=False, indent=2)}
"""


def _source_text_budget(source_count: int) -> int:
    per_source_budget = CUSTOM_NARRATION_SOURCE_TOTAL_CHAR_LIMIT // max(source_count, 1)
    return min(
        LONGFORM_BODY_MAX_CHARS,
        max(CUSTOM_NARRATION_SOURCE_MIN_CHARS, per_source_budget),
    )
