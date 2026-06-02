"""Custom narration source collection and prompt shaping."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.api.audio_episodes import CUSTOM_NARRATION_MAX_CONTENT_IDS
from app.models.contracts import ContentType
from app.models.db import Content, NewsItem
from app.repositories.content_repository import build_visibility_context
from app.services.audio_episode_sources import (
    LONGFORM_BODY_MAX_CHARS,
    build_content_source_payload,
)
from app.services.content_bodies import get_content_body_resolver
from app.services.news_feed import get_visible_news_item
from app.services.prompt_library import render_prompt
from app.utils.news_titles import resolve_news_display_title

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
    news_item_ids: list[int] | None = None,
    mark_source_content_read_on_play: bool = False,
) -> dict[str, Any]:
    """Build a bounded source snapshot for selected long-form and Fast Read sources."""

    normalized_content_ids = _normalize_custom_narration_source_ids(
        content_ids,
        source_label="Content",
    )
    normalized_news_item_ids = _normalize_custom_narration_source_ids(
        news_item_ids or [],
        source_label="Fast Read",
    )
    _validate_custom_narration_source_count(
        content_count=len(normalized_content_ids),
        news_item_count=len(normalized_news_item_ids),
    )
    source_text_budget = _source_text_budget(
        len(normalized_content_ids) + len(normalized_news_item_ids)
    )
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

        source_payload = build_content_source_payload(
            content,
            body_text=body_text,
            source_text_max_chars=source_text_budget,
        )
        source_payload["source_kind"] = "long_form"
        source_items.append(source_payload)

    for news_item_id in normalized_news_item_ids:
        news_item = get_visible_news_item(db, user_id=user_id, news_item_id=news_item_id)
        if news_item is None:
            raise HTTPException(status_code=404, detail=f"Fast Read {news_item_id} not found")
        source_items.append(_build_fast_read_source_payload(news_item))

    return {
        "kind": CUSTOM_NARRATION_KIND,
        "source_count": len(source_items),
        "content_ids": normalized_content_ids,
        "news_item_ids": normalized_news_item_ids,
        "read_on_play": {
            "content_ids": normalized_content_ids if mark_source_content_read_on_play else [],
            "news_item_ids": normalized_news_item_ids,
        },
        "source_text_budget_chars": source_text_budget,
        "source_text_total_chars": sum(
            int(item.get("source_text_chars") or 0) for item in source_items
        ),
        "source_text_included_chars": sum(
            int(item.get("source_text_included_chars") or 0) for item in source_items
        ),
        "items": source_items,
    }


def _normalize_custom_narration_source_ids(
    source_ids: list[int],
    *,
    source_label: str,
) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw_source_id in source_ids:
        try:
            source_id = int(raw_source_id)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail=f"{source_label} ids must be integers",
            ) from None
        if source_id <= 0:
            raise HTTPException(status_code=400, detail=f"{source_label} ids must be positive")
        if source_id in seen:
            continue
        seen.add(source_id)
        normalized.append(source_id)
    return normalized


def _validate_custom_narration_source_count(
    *,
    content_count: int,
    news_item_count: int,
) -> None:
    source_count = content_count + news_item_count
    if source_count < 1:
        raise HTTPException(
            status_code=400,
            detail="Select at least one article, podcast, or Fast Read",
        )
    if source_count > CUSTOM_NARRATION_MAX_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Select at most {CUSTOM_NARRATION_MAX_SOURCES} sources",
        )


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


def _build_fast_read_source_payload(news_item: NewsItem) -> dict[str, Any]:
    news_item_id = _required_int(news_item.id, "Fast Read id")
    key_points = [str(point) for point in (news_item.summary_key_points or []) if str(point)]
    summary = str(news_item.summary_text or "").strip()
    if not summary and not key_points:
        raise HTTPException(
            status_code=400,
            detail=f"No Fast Read summary is available for item {news_item_id}",
        )
    return {
        "source_kind": "fast_read",
        "news_item_id": news_item_id,
        "title": resolve_news_display_title(
            news_item.raw_metadata,
            summary_text=news_item.summary_text,
            fallback=f"Fast Read {news_item_id}",
        ),
        "source": news_item.source_label,
        "platform": news_item.platform,
        "url": news_item.article_url or news_item.canonical_story_url,
        "discussion_url": news_item.discussion_url or news_item.canonical_item_url,
        "publication_date": news_item.published_at.isoformat() if news_item.published_at else None,
        "summary": summary,
        "key_points": key_points,
        "source_text": summary,
        "source_text_excerpt_strategy": "summary",
        "source_text_truncated": False,
        "source_text_chars": len(summary),
        "source_text_included_chars": len(summary),
    }


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
    return render_prompt(
        "audio/episode_scripts#custom_narration_user",
        dialogue_text_char_limit=CUSTOM_NARRATION_DIALOGUE_TEXT_CHAR_LIMIT,
        source_snapshot_json=json.dumps(source_snapshot, ensure_ascii=False, indent=2),
    )


def _source_text_budget(source_count: int) -> int:
    per_source_budget = CUSTOM_NARRATION_SOURCE_TOTAL_CHAR_LIMIT // max(source_count, 1)
    return min(
        LONGFORM_BODY_MAX_CHARS,
        max(CUSTOM_NARRATION_SOURCE_MIN_CHARS, per_source_budget),
    )


def _required_int(value: int | None, field_name: str) -> int:
    if value is None:
        raise ValueError(f"Missing {field_name}")
    return int(value)
