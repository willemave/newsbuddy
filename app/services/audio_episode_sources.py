"""Shared source-shaping helpers for audio episode prompts."""

from __future__ import annotations

from typing import Any

from app.models.db import Content
from app.models.domain.content_mapper import content_to_domain

LONGFORM_BODY_MAX_CHARS = 16_000
LONGFORM_BODY_HEAD_CHARS = 7_000
LONGFORM_BODY_MIDDLE_CHARS = 4_000
LONGFORM_BODY_TAIL_CHARS = 5_000


def excerpt_longform_source_text(
    body_text: str,
    *,
    max_chars: int = LONGFORM_BODY_MAX_CHARS,
) -> tuple[str, str]:
    """Keep long-form source prompts bounded while preserving source coverage."""

    normalized = body_text.strip()
    if len(normalized) <= max_chars:
        return normalized, "full"

    head_chars, middle_chars, tail_chars = _excerpt_segment_lengths(max_chars)
    head = normalized[:head_chars].rstrip()
    middle_start = max((len(normalized) - middle_chars) // 2, 0)
    middle = normalized[middle_start : middle_start + middle_chars].strip()
    tail = normalized[-tail_chars:].lstrip()
    return (
        "\n\n[Source opening excerpt]\n"
        f"{head}"
        "\n\n[Source middle excerpt]\n"
        f"{middle}"
        "\n\n[Source closing excerpt]\n"
        f"{tail}",
        "head_middle_tail",
    )


def build_content_source_payload(
    content: Content,
    *,
    body_text: str,
    source_text_max_chars: int = LONGFORM_BODY_MAX_CHARS,
) -> dict[str, Any]:
    """Build the common source payload used by long-form audio prompts."""

    metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
    source_text, excerpt_strategy = excerpt_longform_source_text(
        body_text,
        max_chars=source_text_max_chars,
    )
    normalized_body = body_text.strip()
    return {
        "content_id": _required_int(content.id, "content id"),
        "content_type": str(content.content_type),
        "title": content_display_title(content),
        "source": content.source,
        "platform": content.platform,
        "url": content.url,
        "publication_date": content.publication_date.isoformat()
        if content.publication_date
        else None,
        "summary": extract_content_summary(metadata, content=content),
        "source_text": source_text,
        "source_text_excerpt_strategy": excerpt_strategy,
        "source_text_truncated": len(normalized_body) > source_text_max_chars,
        "source_text_chars": len(normalized_body),
        "source_text_included_chars": len(source_text),
    }


def content_display_title(content: Content) -> str:
    try:
        return content_to_domain(content).display_title
    except Exception:
        return (content.title or "").strip() or f"Content {content.id}"


def extract_content_summary(metadata: dict[str, Any], *, content: Content) -> dict[str, Any]:
    summary = metadata.get("summary")
    if isinstance(summary, dict):
        overview = first_text(
            summary.get("overview"),
            summary.get("short_summary"),
            summary.get("summary"),
            summary.get("narrative"),
            summary.get("text"),
        )
        return {
            "overview": overview or content.short_summary,
            "key_points": extract_summary_points(summary),
            "raw": summary,
        }
    if isinstance(summary, str):
        return {"overview": summary.strip(), "key_points": []}
    return {"overview": content.short_summary, "key_points": []}


def extract_summary_points(summary: dict[str, Any]) -> list[str]:
    for key in ("key_points", "bullet_points", "points", "insights"):
        raw_value = summary.get(key)
        if not isinstance(raw_value, list):
            continue
        points: list[str] = []
        for item in raw_value:
            text: str | None
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = first_text(
                    item.get("text"),
                    item.get("point"),
                    item.get("content"),
                    item.get("insight"),
                )
            else:
                text = None
            if text:
                points.append(text)
        if points:
            return points[:10]
    return []


def first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _excerpt_segment_lengths(max_chars: int) -> tuple[int, int, int]:
    if max_chars >= LONGFORM_BODY_MAX_CHARS:
        return LONGFORM_BODY_HEAD_CHARS, LONGFORM_BODY_MIDDLE_CHARS, LONGFORM_BODY_TAIL_CHARS

    bounded = max(max_chars, 1_000)
    head = max(int(bounded * 0.44), 400)
    middle = max(int(bounded * 0.25), 250)
    tail = max(bounded - head - middle, 350)
    return head, middle, tail


def _required_int(value: int | None, field_name: str) -> int:
    if value is None:
        raise ValueError(f"Missing {field_name}")
    return int(value)
