"""Saved-knowledge search helpers shared by assistant features."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Text, cast, or_
from sqlalchemy.orm import Session

from app.models.schema import Content, ContentKnowledgeSave
from app.utils.summary_utils import extract_summary_text

MAX_KNOWLEDGE_HITS = 5
MAX_TRANSCRIPT_EXCERPT_CHARS = 280


@dataclass
class KnowledgeHit:
    """Single hit from user-saved knowledge content."""

    content_id: int
    title: str
    url: str
    source: str | None
    content_type: str
    summary: str | None
    transcript_excerpt: str | None


def _require_content_id(content: Content) -> int:
    """Return a persisted content ID or raise."""
    content_id = content.id
    if content_id is None:
        raise ValueError("Content must be persisted before use")
    return content_id


def search_knowledge(
    db: Session,
    user_id: int,
    query: str,
    limit: int = MAX_KNOWLEDGE_HITS,
) -> list[KnowledgeHit]:
    """Search user-saved knowledge content with deterministic SQL matching."""
    normalized_query = query.strip()
    if not normalized_query:
        return []

    max_hits = max(1, min(limit, 20))
    pattern = f"%{normalized_query}%"

    base_query = (
        db.query(Content)
        .join(ContentKnowledgeSave, ContentKnowledgeSave.content_id == Content.id)
        .filter(ContentKnowledgeSave.user_id == user_id)
    )
    rows = (
        base_query.filter(
            or_(
                Content.title.ilike(pattern),
                Content.source.ilike(pattern),
                Content.url.ilike(pattern),
                cast(Content.content_metadata, Text).ilike(pattern),
            )
        )
        .order_by(ContentKnowledgeSave.saved_at.desc())
        .limit(max_hits)
        .all()
    )
    if not rows:
        rows = base_query.order_by(ContentKnowledgeSave.saved_at.desc()).limit(max_hits).all()

    hits: list[KnowledgeHit] = []
    for content in rows:
        metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
        hits.append(
            KnowledgeHit(
                content_id=_require_content_id(content),
                title=str(content.title or "Untitled"),
                url=str(content.url or ""),
                source=str(content.source) if content.source else None,
                content_type=str(content.content_type or "unknown"),
                summary=_extract_summary(metadata),
                transcript_excerpt=_extract_transcript_excerpt(metadata),
            )
        )

    return hits


def _extract_summary(metadata: dict[str, object]) -> str | None:
    """Extract a concise summary text from content metadata."""
    summary_payload = metadata.get("summary")
    if summary_payload is not None and not isinstance(summary_payload, (dict, str)):
        return None
    summary = extract_summary_text(summary_payload)
    if not summary:
        return None
    trimmed = str(summary).strip()
    return trimmed or None


def _extract_transcript_excerpt(metadata: dict[str, object]) -> str | None:
    """Extract a bounded transcript excerpt when available."""
    transcript = metadata.get("transcript") or metadata.get("excerpt")
    if not isinstance(transcript, str):
        return None
    excerpt = transcript.strip()
    if not excerpt:
        return None
    if len(excerpt) > MAX_TRANSCRIPT_EXCERPT_CHARS:
        return excerpt[:MAX_TRANSCRIPT_EXCERPT_CHARS].rstrip() + "..."
    return excerpt
