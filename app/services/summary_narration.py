"""Helpers for building deterministic spoken narration from content summaries."""

from __future__ import annotations

from app.models.db import Content

MAX_NARRATION_POINT_CHARS = 280
MAX_NARRATION_POINTS = 10
MAX_NARRATION_CHARS = 8_000


def _truncate_with_ellipsis(text: str, limit: int) -> str:
    suffix = "..."
    if limit <= len(suffix):
        return suffix[:limit]
    return f"{text[: limit - len(suffix)].rstrip()}{suffix}"


def _truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    if len(cleaned) <= limit:
        return cleaned
    return _truncate_with_ellipsis(cleaned, limit)


def _clean_spoken_text(value: object, limit: int = MAX_NARRATION_POINT_CHARS) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        return None
    if len(cleaned) <= limit:
        return cleaned
    return _truncate_with_ellipsis(cleaned, limit)


def _extract_artifact_payload(summary_payload: object) -> dict[str, object] | None:
    if not isinstance(summary_payload, dict):
        return None
    artifact = summary_payload.get("artifact")
    if not isinstance(artifact, dict):
        return None
    payload = artifact.get("payload")
    if not isinstance(payload, dict):
        return None
    return payload


def _extract_spoken_points(summary_payload: object, metadata: dict[str, object]) -> list[str]:
    points: list[str] = []

    def append_point(candidate: object) -> None:
        if not isinstance(candidate, dict):
            cleaned = _clean_spoken_text(candidate)
            if cleaned:
                points.append(cleaned)
            return

        text = (
            _clean_spoken_text(candidate.get("point"))
            or _clean_spoken_text(candidate.get("text"))
            or _clean_spoken_text(candidate.get("insight"))
        )
        heading = _clean_spoken_text(candidate.get("heading"), limit=100)
        content = _clean_spoken_text(candidate.get("content"), limit=MAX_NARRATION_POINT_CHARS + 80)
        detail = _clean_spoken_text(candidate.get("detail"), limit=MAX_NARRATION_POINT_CHARS + 80)
        topic = _clean_spoken_text(candidate.get("topic"), limit=80)

        if not text and heading and content:
            text = f"{heading}: {content}"
        elif not text:
            text = heading or content
        if text and detail and detail not in text:
            text = f"{text} {detail}"
        if topic and text and topic.lower() not in text.lower():
            text = f"{topic}: {text}"
        if text:
            points.append(text)

    if isinstance(summary_payload, dict):
        for collection_key in ("key_points", "bullet_points", "points", "insights"):
            collection = summary_payload.get(collection_key)
            if isinstance(collection, list):
                for item in collection:
                    append_point(item)

        topics = summary_payload.get("topics")
        if isinstance(topics, list):
            for topic_item in topics:
                if not isinstance(topic_item, dict):
                    continue
                topic_label = _clean_spoken_text(topic_item.get("topic"), limit=80)
                bullets = topic_item.get("bullets")
                if not isinstance(bullets, list):
                    continue
                for bullet in bullets:
                    if isinstance(bullet, dict):
                        bullet_text = _clean_spoken_text(bullet.get("text"))
                    else:
                        bullet_text = _clean_spoken_text(bullet)
                    if not bullet_text:
                        continue
                    if topic_label:
                        points.append(f"{topic_label}: {bullet_text}")
                    else:
                        points.append(bullet_text)

    top_level_bullets = metadata.get("bullet_points")
    if isinstance(top_level_bullets, list):
        for item in top_level_bullets:
            append_point(item)

    deduped: list[str] = []
    seen: set[str] = set()
    for point in points:
        normalized = point.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(point)
        if len(deduped) >= MAX_NARRATION_POINTS:
            break
    return deduped


def build_summary_narration(content: Content, *, title: str) -> str:
    """Build deterministic spoken summary text for one content item."""

    metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
    summary_payload = metadata.get("summary")

    narrative: str | None = None
    takeaway: str | None = None
    if isinstance(summary_payload, str):
        narrative = _clean_spoken_text(summary_payload, limit=3_600)
    elif isinstance(summary_payload, dict):
        artifact_payload = _extract_artifact_payload(summary_payload)
        if artifact_payload:
            narrative = _clean_spoken_text(artifact_payload.get("overview"), limit=3_600)
            takeaway = _clean_spoken_text(artifact_payload.get("takeaway"), limit=700)
            summary_payload = artifact_payload
        else:
            narrative = (
                _clean_spoken_text(summary_payload.get("editorial_narrative"), limit=3_600)
                or _clean_spoken_text(summary_payload.get("overview"), limit=2_400)
                or _clean_spoken_text(summary_payload.get("summary"), limit=2_400)
                or _clean_spoken_text(summary_payload.get("hook"), limit=2_400)
            )
            takeaway = _clean_spoken_text(summary_payload.get("takeaway"), limit=700)

    if not narrative:
        narrative = _truncate(content.short_summary, 2_400)

    key_points = _extract_spoken_points(summary_payload, metadata)

    parts: list[str] = [f"Here is the full summary for {title}."]
    if narrative:
        parts.append(narrative)

    if key_points:
        numbered_points = " ".join(
            f"Point {idx}: {point}" for idx, point in enumerate(key_points, start=1)
        )
        parts.append(f"Key points. {numbered_points}")

    if takeaway and (not narrative or takeaway.lower() not in narrative.lower()):
        parts.append(f"Takeaway: {takeaway}")

    if not narrative and not key_points:
        parts.append("I don't have a complete processed summary yet.")

    spoken_text = " ".join(part.strip() for part in parts if part.strip()).strip()
    if len(spoken_text) <= MAX_NARRATION_CHARS:
        return spoken_text
    return _truncate_with_ellipsis(spoken_text, MAX_NARRATION_CHARS)
