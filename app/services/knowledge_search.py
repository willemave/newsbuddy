"""Saved-knowledge search helpers shared by assistant features."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, literal, or_
from sqlalchemy.orm import Session

from app.models.db import AgentDataFile, Content, ContentKnowledgeSave
from app.repositories.content_search_expressions import (
    content_search_expressions,
    content_search_text_expression,
    content_source_expression,
    content_summary_title_expression,
    content_title_expression,
)
from app.utils.summary_utils import extract_summary_text

MAX_KNOWLEDGE_HITS = 8
MAX_KNOWLEDGE_QUERY_CHARS = 500
MAX_SNIPPET_CHARS = 600


@dataclass(frozen=True)
class KnowledgeHit:
    """Single ranked hit from user-saved knowledge content."""

    content_id: int
    title: str
    url: str
    source: str | None
    content_type: str
    saved_at: datetime
    snippet: str | None
    corpus_path: str | None


def search_knowledge(
    db: Session,
    user_id: int,
    query: str,
    limit: int = MAX_KNOWLEDGE_HITS,
) -> list[KnowledgeHit]:
    """Return ranked matches from one user's saved library; never return unrelated saves."""

    normalized_query = " ".join(query.split()).strip()[:MAX_KNOWLEDGE_QUERY_CHARS]
    if not normalized_query:
        return []

    max_hits = max(1, min(limit, 20))
    corpus_path = (
        db.query(AgentDataFile.path)
        .filter(
            AgentDataFile.user_id == user_id,
            AgentDataFile.document_kind == "content",
            AgentDataFile.document_key == func.cast(Content.id, AgentDataFile.document_key.type),
            AgentDataFile.deleted_at.is_(None),
        )
        .correlate(Content)
        .scalar_subquery()
    )
    base_query = (
        db.query(Content, ContentKnowledgeSave.saved_at, corpus_path.label("corpus_path"))
        .join(ContentKnowledgeSave, ContentKnowledgeSave.content_id == Content.id)
        .filter(ContentKnowledgeSave.user_id == user_id)
    )

    if _uses_postgres(db):
        expressions = content_search_expressions(normalized_query)
        headline = func.ts_headline(
            "english",
            content_search_text_expression(),
            expressions.tsquery,
            "StartSel=**, StopSel=**, MaxWords=40, MinWords=12, ShortWord=3",
        )
        rows = (
            base_query.add_columns(headline.label("headline"))
            .filter(expressions.matches)
            .order_by(expressions.rank.desc(), ContentKnowledgeSave.saved_at.desc())
            .limit(max_hits)
            .all()
        )
    else:
        tokens = [token for token in normalized_query.lower().split() if len(token) >= 2]
        if not tokens:
            return []
        filters = [
            or_(
                func.lower(content_summary_title_expression()).like(f"%{token}%"),
                func.lower(content_title_expression()).like(f"%{token}%"),
                func.lower(content_source_expression()).like(f"%{token}%"),
                func.lower(content_search_text_expression()).like(f"%{token}%"),
            )
            for token in tokens
        ]
        rows = (
            base_query.add_columns(literal(None).label("headline"))
            .filter(and_(*filters))
            .order_by(ContentKnowledgeSave.saved_at.desc())
            .limit(max_hits)
            .all()
        )

    return [
        _build_hit(content, saved_at=saved_at, corpus_path=path, headline=headline)
        for content, saved_at, path, headline in rows
    ]


def _uses_postgres(db: Session) -> bool:
    bind = db.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


def _build_hit(
    content: Content,
    *,
    saved_at: datetime,
    corpus_path: str | None,
    headline: str | None,
) -> KnowledgeHit:
    content_id = content.id
    if content_id is None:
        raise ValueError("Content must be persisted before use")
    metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
    snippet = _clean_snippet(headline) or _extract_summary(metadata)
    return KnowledgeHit(
        content_id=content_id,
        title=str(content.title or "Untitled"),
        url=str(content.url or ""),
        source=str(content.source) if content.source else None,
        content_type=str(content.content_type or "unknown"),
        saved_at=saved_at,
        snippet=snippet,
        corpus_path=f"/data/{corpus_path.lstrip('/')}" if corpus_path else None,
    )


def _clean_snippet(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(value.split()).strip()
    if len(cleaned) > MAX_SNIPPET_CHARS:
        return f"{cleaned[: MAX_SNIPPET_CHARS - 3].rstrip()}..."
    return cleaned or None


def _extract_summary(metadata: dict[str, object]) -> str | None:
    summary_payload = metadata.get("summary")
    if summary_payload is not None and not isinstance(summary_payload, (dict, str)):
        return None
    summary = extract_summary_text(summary_payload)
    return _clean_snippet(str(summary)) if summary else None
