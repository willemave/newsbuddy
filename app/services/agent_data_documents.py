"""Render the credential-free, per-user corpus mounted in agent VMs."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import PurePosixPath

import yaml
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.contracts import (
    AgentDataBackfillStage,
    ContentStatus,
    MessageProcessingStatus,
    NewsItemStatus,
)
from app.models.db import (
    BriefingSegment,
    ChatMessage,
    ChatSession,
    Content,
    ContentKnowledgeSave,
    ContentStatusEntry,
    NewsItem,
)
from app.services.content_bodies import ContentBodyVariant, get_content_body_resolver
from app.services.news_feed import build_visible_news_item_filter
from app.utils.news_titles import resolve_news_display_title
from app.utils.summary_utils import extract_summary_text
from app.utils.title_utils import resolve_content_display_title

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_TRUNCATION_MARKER = "\n\n[Newsly: document truncated at the per-file byte limit.]\n"


@dataclass(frozen=True)
class AgentDataDocument:
    """One deterministic file and its searchable metadata."""

    document_kind: str
    document_key: str
    path: str
    content_bytes: bytes
    checksum_sha256: str
    metadata: dict[str, object]

    @property
    def text(self) -> str:
        return self.content_bytes.decode("utf-8")

    @property
    def byte_size(self) -> int:
        return len(self.content_bytes)

    def index_record(self) -> dict[str, object]:
        return {
            **self.metadata,
            "path": self.path,
            "checksum_sha256": self.checksum_sha256,
            "byte_size": self.byte_size,
        }


@dataclass(frozen=True)
class AgentDataBackfillPage:
    """One bounded corpus page and the cursor for the following task."""

    stage: AgentDataBackfillStage
    ids: tuple[int, ...]
    next_stage: AgentDataBackfillStage | None
    next_before_id: int | None


def next_agent_data_backfill_page(
    db: Session,
    *,
    user_id: int,
    stage: AgentDataBackfillStage | None,
    before_id: int | None,
    limit: int,
) -> AgentDataBackfillPage | None:
    """Return recent-first bounded pages without materializing the whole corpus."""
    stages = tuple(AgentDataBackfillStage)
    current_stage: AgentDataBackfillStage | None
    try:
        current_stage = (
            AgentDataBackfillStage(stage) if stage is not None else AgentDataBackfillStage.KNOWLEDGE
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unsupported agent-data backfill stage: {stage}") from exc
    cursor = before_id
    while current_stage is not None:
        rows = _backfill_stage_rows(
            db,
            user_id=user_id,
            stage=current_stage,
            before_id=cursor,
            limit=limit,
        )
        if rows:
            ids = tuple(rows)
            next_before_id = min(ids)
            return AgentDataBackfillPage(
                stage=current_stage,
                ids=ids,
                next_stage=current_stage,
                next_before_id=next_before_id,
            )
        stage_index = stages.index(current_stage) + 1
        current_stage = stages[stage_index] if stage_index < len(stages) else None
        cursor = None
    return None


def briefing_dates_for_backfill_page(
    db: Session,
    *,
    user_id: int,
    segment_ids: tuple[int, ...],
) -> frozenset[str]:
    if not segment_ids:
        return frozenset()
    rows = (
        db.query(BriefingSegment.created_at)
        .filter(
            BriefingSegment.user_id == user_id,
            BriefingSegment.id.in_(segment_ids),
        )
        .all()
    )
    return frozenset(_date_key(created_at) for (created_at,) in rows)


def _backfill_stage_rows(
    db: Session,
    *,
    user_id: int,
    stage: AgentDataBackfillStage,
    before_id: int | None,
    limit: int,
) -> list[int]:
    if stage == AgentDataBackfillStage.KNOWLEDGE:
        query = (
            db.query(ContentKnowledgeSave.content_id)
            .join(Content, Content.id == ContentKnowledgeSave.content_id)
            .filter(
                ContentKnowledgeSave.user_id == user_id,
                Content.status == ContentStatus.COMPLETED.value,
            )
        )
        id_column = ContentKnowledgeSave.content_id
    elif stage == AgentDataBackfillStage.CONTENT:
        query = db.query(Content.id).filter(
            Content.status == ContentStatus.COMPLETED.value,
            Content.id.in_(_visible_content_ids_query(db, user_id=user_id)),
            Content.id.not_in(
                db.query(ContentKnowledgeSave.content_id).filter(
                    ContentKnowledgeSave.user_id == user_id
                )
            ),
        )
        id_column = Content.id
    elif stage == AgentDataBackfillStage.NEWS:
        query = db.query(NewsItem.id).filter(
            build_visible_news_item_filter(db, user_id=user_id),
            NewsItem.status == NewsItemStatus.READY.value,
            NewsItem.representative_news_item_id.is_(None),
        )
        id_column = NewsItem.id
    elif stage == AgentDataBackfillStage.CHATS:
        query = db.query(ChatSession.id).filter(ChatSession.user_id == user_id)
        id_column = ChatSession.id
    elif stage == AgentDataBackfillStage.BRIEFINGS:
        query = db.query(BriefingSegment.id).filter(
            BriefingSegment.user_id == user_id,
            BriefingSegment.status.in_(("active", "degraded")),
        )
        id_column = BriefingSegment.id
    if before_id is not None:
        query = query.filter(id_column < before_id)
    return [int(row_id) for (row_id,) in query.order_by(id_column.desc()).limit(limit)]


def _visible_content_ids_query(db: Session, *, user_id: int):
    return (
        db.query(ContentStatusEntry.content_id)
        .filter(ContentStatusEntry.user_id == user_id)
        .union(
            db.query(ContentKnowledgeSave.content_id).filter(
                ContentKnowledgeSave.user_id == user_id
            )
        )
        .union(
            db.query(ChatSession.content_id).filter(
                ChatSession.user_id == user_id,
                ChatSession.content_id.is_not(None),
            )
        )
    )


def collect_agent_data_documents(
    db: Session,
    *,
    user_id: int,
    content_ids: set[int] | None = None,
    news_item_ids: set[int] | None = None,
    chat_session_ids: set[int] | None = None,
    briefing_dates: set[str] | None = None,
) -> list[AgentDataDocument]:
    """Render a precisely selected, bounded corpus slice."""
    documents: list[AgentDataDocument] = []
    if content_ids:
        documents.extend(_content_documents(db, user_id=user_id, content_ids=content_ids))
    if news_item_ids:
        documents.extend(_news_documents(db, user_id=user_id, news_item_ids=news_item_ids))
    if chat_session_ids:
        documents.extend(
            _chat_documents(
                db,
                user_id=user_id,
                chat_session_ids=chat_session_ids,
            )
        )
    if briefing_dates:
        documents.extend(
            _briefing_documents(
                db,
                user_id=user_id,
                date_keys=briefing_dates,
            )
        )
    return sorted(documents, key=lambda document: document.path)


def _content_documents(
    db: Session,
    *,
    user_id: int,
    content_ids: set[int],
) -> list[AgentDataDocument]:
    query = db.query(Content).filter(
        Content.status == ContentStatus.COMPLETED.value,
        Content.id.in_(_visible_content_ids_query(db, user_id=user_id)),
    )
    query = query.filter(Content.id.in_(content_ids))

    saved_ids = {
        int(row[0])
        for row in db.query(ContentKnowledgeSave.content_id)
        .filter(
            ContentKnowledgeSave.user_id == user_id,
            ContentKnowledgeSave.content_id.in_(content_ids),
        )
        .all()
    }
    resolver = get_content_body_resolver()
    documents: list[AgentDataDocument] = []
    for content in query.order_by(Content.id).yield_per(100):
        content_id = _require_id(content.id, "content")
        rendered = resolver.resolve(db, content=content, variant=ContentBodyVariant.RENDERED)
        source = resolver.resolve(db, content=content, variant=ContentBodyVariant.SOURCE)
        summary_text = (
            (rendered.text if rendered else None)
            or extract_summary_text((content.content_metadata or {}).get("summary"))
            or ""
        )
        full_body = (source.text if source else None) or ""
        body_parts: list[str] = []
        if summary_text:
            body_parts.append(f"## Summary\n\n{summary_text}")
        if full_body and full_body.strip() != summary_text.strip():
            body_parts.append(f"## Content\n\n{full_body}")
        body = "\n\n".join(body_parts)
        published = content.publication_date or content.processed_at or content.created_at
        title = resolve_content_display_title(
            title=content.title,
            metadata=content.content_metadata,
            fallback=f"Content {content_id}",
        )
        kind = str(content.content_type or "content")
        saved = content_id in saved_ids
        path_root = "knowledge" if saved else f"content/{_month_key(published)}"
        metadata = {
            "id": content_id,
            "kind": kind,
            "title": title,
            "url": content.url,
            "published_at": _iso(published),
            "source": content.source,
            "tags": [kind, "saved"] if saved else [kind],
            "saved": saved,
        }
        path = f"{path_root}/{_slug(title)}--content-{content_id}.md"
        documents.append(
            _document(
                document_kind="content",
                document_key=str(content_id),
                path=path,
                metadata=metadata,
                body=body,
            )
        )
    return documents


def _news_documents(
    db: Session,
    *,
    user_id: int,
    news_item_ids: set[int],
) -> list[AgentDataDocument]:
    query = db.query(NewsItem).filter(
        build_visible_news_item_filter(db, user_id=user_id),
        NewsItem.status == NewsItemStatus.READY.value,
        NewsItem.representative_news_item_id.is_(None),
    )
    query = query.filter(NewsItem.id.in_(news_item_ids))
    items = query.order_by(NewsItem.id).all()
    legacy_content_ids = {
        int(item.legacy_content_id) for item in items if isinstance(item.legacy_content_id, int)
    }
    legacy_contents = {
        _require_id(content.id, "content"): content
        for content in (
            db.query(Content)
            .filter(
                Content.id.in_(legacy_content_ids),
                Content.status == ContentStatus.COMPLETED.value,
            )
            .all()
            if legacy_content_ids
            else []
        )
    }
    resolver = get_content_body_resolver()
    documents: list[AgentDataDocument] = []
    for item in items:
        item_id = _require_id(item.id, "news item")
        published = item.published_at or item.processed_at or item.ingested_at
        title = resolve_news_display_title(
            item.raw_metadata,
            summary_text=item.summary_text,
            fallback=f"News item {item_id}",
        )
        url = (
            item.article_url
            or item.canonical_story_url
            or item.discussion_url
            or item.canonical_item_url
        )
        points = "\n".join(
            f"- {str(point).strip()}"
            for point in (item.summary_key_points or [])
            if str(point).strip()
        )
        enriched_body = ""
        legacy_content = legacy_contents.get(int(item.legacy_content_id or 0))
        if legacy_content is not None:
            rendered = resolver.resolve(
                db,
                content=legacy_content,
                variant=ContentBodyVariant.RENDERED,
            )
            source = resolver.resolve(
                db,
                content=legacy_content,
                variant=ContentBodyVariant.SOURCE,
            )
            enriched_body = (rendered.text if rendered else None) or (source.text if source else "")
        body_parts = [part for part in (item.summary_text or "", points) if part]
        if enriched_body:
            body_parts.append(f"## Enriched article body\n\n{enriched_body}")
        body = "\n\n".join(body_parts)
        metadata = {
            "id": item_id,
            "kind": "news",
            "title": title,
            "url": url,
            "published_at": _iso(published),
            "source": item.source_label or item.platform,
            "tags": ["news", str(item.platform or "unknown")],
            "saved": False,
        }
        path = f"news/{_date_key(published)}/{_slug(title)}--news-{item_id}.md"
        documents.append(
            _document(
                document_kind="news",
                document_key=str(item_id),
                path=path,
                metadata=metadata,
                body=body,
            )
        )
    return documents


def _chat_documents(
    db: Session,
    *,
    user_id: int,
    chat_session_ids: set[int],
) -> list[AgentDataDocument]:
    sessions = (
        db.query(ChatSession)
        .filter(
            ChatSession.user_id == user_id,
            ChatSession.id.in_(chat_session_ids),
        )
        .order_by(ChatSession.id)
        .all()
    )
    session_ids = [_require_id(session.id, "chat session") for session in sessions]
    messages_by_session: dict[int, list[ChatMessage]] = defaultdict(list)
    if session_ids:
        rows = (
            db.query(ChatMessage)
            .filter(
                ChatMessage.session_id.in_(session_ids),
                ChatMessage.status == MessageProcessingStatus.COMPLETED.value,
            )
            .order_by(ChatMessage.session_id, ChatMessage.created_at, ChatMessage.id)
            .all()
        )
        for row in rows:
            message_session_id = _require_id(row.session_id, "chat message session")
            messages_by_session[message_session_id].append(row)

    documents: list[AgentDataDocument] = []
    for session in sessions:
        session_id = _require_id(session.id, "chat session")
        transcript = "\n\n".join(
            _render_message_json(row.message_list) for row in messages_by_session[session_id]
        ).strip()
        if not transcript:
            continue
        title = session.title or session.topic or f"Chat {session_id}"
        metadata = {
            "id": session_id,
            "kind": "chat",
            "title": title,
            "url": None,
            "published_at": _iso(session.created_at),
            "source": "Newsly chat",
            "tags": ["chat", str(session.session_type or "general")],
            "saved": False,
        }
        documents.append(
            _document(
                document_kind="chat",
                document_key=str(session_id),
                path=f"chats/{session_id}-{_slug(title)}.md",
                metadata=metadata,
                body=transcript,
            )
        )
    return documents


def _briefing_documents(
    db: Session,
    *,
    user_id: int,
    date_keys: set[str],
) -> list[AgentDataDocument]:
    query = db.query(BriefingSegment).filter(
        BriefingSegment.user_id == user_id,
        BriefingSegment.status.in_(("active", "degraded")),
    )
    bounds = [_parse_date_key(value) for value in date_keys]
    clauses = [
        (BriefingSegment.created_at >= start) & (BriefingSegment.created_at < end)
        for start, end in bounds
    ]
    query = query.filter(or_(*clauses))
    grouped: dict[str, list[BriefingSegment]] = {}
    for segment in query.order_by(BriefingSegment.created_at, BriefingSegment.id).all():
        grouped.setdefault(_date_key(segment.created_at), []).append(segment)
    documents: list[AgentDataDocument] = []
    for date_key, segments in grouped.items():
        body = "\n\n---\n\n".join(
            (segment.markdown_raw or "").strip() or (segment.narration_text or "").strip()
            for segment in segments
            if (segment.markdown_raw or "").strip() or (segment.narration_text or "").strip()
        )
        if not body:
            continue
        metadata: dict[str, object] = {
            "id": date_key,
            "kind": "briefing",
            "title": f"Newsly Briefing — {date_key}",
            "url": None,
            "published_at": date_key,
            "source": "Newsly Briefing",
            "tags": ["briefing"],
            "saved": False,
            "segment_ids": [_require_id(segment.id, "briefing segment") for segment in segments],
        }
        documents.append(
            _document(
                document_kind="briefing",
                document_key=date_key,
                path=f"briefings/{date_key}.md",
                metadata=metadata,
                body=body,
            )
        )
    return documents


def _document(
    *,
    document_kind: str,
    document_key: str,
    path: str,
    metadata: dict[str, object],
    body: str,
) -> AgentDataDocument:
    clean_path = PurePosixPath(path).as_posix().lstrip("/")
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()
    text = f"---\n{frontmatter}\n---\n\n{body.strip()}\n"
    max_bytes = get_settings().agent_data_document_max_bytes
    encoded = text.encode("utf-8")
    if len(encoded) > max_bytes:
        marker = _TRUNCATION_MARKER.encode("utf-8")
        text = (
            encoded[: max_bytes - len(marker)].decode("utf-8", errors="ignore") + _TRUNCATION_MARKER
        )
        encoded = text.encode("utf-8")
    return AgentDataDocument(
        document_kind=document_kind,
        document_key=document_key,
        path=clean_path,
        content_bytes=encoded,
        checksum_sha256=sha256(encoded).hexdigest(),
        metadata=metadata,
    )


def _require_id(value: int | None, record_kind: str) -> int:
    if value is None:
        raise RuntimeError(f"{record_kind.capitalize()} must be persisted before corpus rendering")
    return value


def _render_message_json(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        messages = json.loads(value)
    except json.JSONDecodeError:
        return ""
    lines: list[str] = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        role = "Assistant" if message.get("kind") == "response" else "User"
        contents = [
            str(part.get("content") or "").strip()
            for part in message.get("parts", [])
            if isinstance(part, dict)
            and part.get("part_kind") in {"text", "user-prompt"}
            and str(part.get("content") or "").strip()
        ]
        if contents:
            lines.append(f"## {role}\n\n" + "\n\n".join(contents))
    return "\n\n".join(lines)


def _slug(value: object) -> str:
    slug = _SLUG_RE.sub("-", str(value or "untitled").lower()).strip("-")
    return slug[:80] or "untitled"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _date_key(value: datetime | None) -> str:
    return (value or datetime.now(UTC)).strftime("%Y-%m-%d")


def _month_key(value: datetime | None) -> str:
    return (value or datetime.now(UTC)).strftime("%Y/%m")


def _parse_date_key(value: str) -> tuple[datetime, datetime]:
    from datetime import timedelta

    start = datetime.strptime(value, "%Y-%m-%d")
    return start, start + timedelta(days=1)
