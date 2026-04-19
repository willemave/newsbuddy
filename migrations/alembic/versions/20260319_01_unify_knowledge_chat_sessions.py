"""Unify legacy user chat sessions under knowledge_chat.

Revision ID: 20260319_01
Revises: 20260317_01
Create Date: 2026-03-19
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "20260319_01"
down_revision = "20260317_01"
branch_labels = None
depends_on = None

KNOWLEDGE_SESSION_TYPE = "knowledge_chat"
LEGACY_SESSION_TYPES = ("assistant_quick", "article_brain", "topic", "voice_live")


def _coerce_metadata(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _truncate_text(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    collapsed = " ".join(value.strip().split())
    if not collapsed:
        return None
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: limit - 3].rstrip()}..."


def _extract_short_summary(metadata: dict[str, object]) -> str | None:
    summary = metadata.get("summary")
    if isinstance(summary, dict):
        for key in ("overview", "summary", "hook", "title"):
            value = summary.get(key)
            if isinstance(value, str):
                truncated = _truncate_text(value, 280)
                if truncated:
                    return truncated
    if isinstance(summary, str):
        return _truncate_text(summary, 280)
    return None


def _extract_transcript_excerpt(metadata: dict[str, object]) -> str | None:
    summary = metadata.get("summary")
    full_markdown = summary.get("full_markdown") if isinstance(summary, dict) else None
    for candidate in (metadata.get("transcript"), metadata.get("content"), full_markdown):
        if isinstance(candidate, str):
            truncated = _truncate_text(candidate, 420)
            if truncated:
                return truncated
    return None


def _build_context_snapshot(
    *,
    session_row: sa.Row,
    content_row: sa.Row | None,
) -> str:
    lines = [
        f"Screen Type: {KNOWLEDGE_SESSION_TYPE}",
        "Screen Title: Knowledge",
    ]
    if session_row.topic:
        lines.append(f"Selected Topic: {session_row.topic}")
    if session_row.title:
        lines.append(f"Client Note: {str(session_row.title)[:500]}")

    if content_row is not None:
        lines.append("Visible Content:")
        label = (content_row.title or "Untitled").strip()
        source = f" ({content_row.source})" if content_row.source else ""
        lines.append(f"- [{content_row.id}] {label}{source} — {content_row.url}")

        metadata = _coerce_metadata(content_row.content_metadata)
        short_summary = _extract_short_summary(metadata)
        if short_summary:
            lines.append(f"  Short Summary: {short_summary}")
        transcript_excerpt = _extract_transcript_excerpt(metadata)
        if transcript_excerpt:
            lines.append(f"  Transcript Excerpt: {transcript_excerpt}")

    lines.append(f"User ID: {session_row.user_id}")
    return "\n".join(lines)


def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)

    chat_sessions = sa.table(
        "chat_sessions",
        sa.column("id", sa.Integer),
        sa.column("user_id", sa.Integer),
        sa.column("content_id", sa.Integer),
        sa.column("title", sa.String),
        sa.column("topic", sa.String),
        sa.column("session_type", sa.String),
        sa.column("context_snapshot", sa.Text),
    )
    contents = sa.table(
        "contents",
        sa.column("id", sa.Integer),
        sa.column("url", sa.String),
        sa.column("title", sa.String),
        sa.column("source", sa.String),
        sa.column("content_metadata", sa.JSON),
    )

    legacy_rows = session.execute(
        sa.select(
            chat_sessions.c.id,
            chat_sessions.c.user_id,
            chat_sessions.c.content_id,
            chat_sessions.c.title,
            chat_sessions.c.topic,
            chat_sessions.c.session_type,
            chat_sessions.c.context_snapshot,
        ).where(chat_sessions.c.session_type.in_(LEGACY_SESSION_TYPES))
    ).all()

    session.execute(
        sa.update(chat_sessions)
        .where(chat_sessions.c.session_type.in_(LEGACY_SESSION_TYPES))
        .values(session_type=KNOWLEDGE_SESSION_TYPE)
    )

    content_ids = sorted({row.content_id for row in legacy_rows if row.content_id is not None})
    content_by_id: dict[int, sa.Row] = {}
    if content_ids:
        content_rows = session.execute(
            sa.select(
                contents.c.id,
                contents.c.url,
                contents.c.title,
                contents.c.source,
                contents.c.content_metadata,
            ).where(contents.c.id.in_(content_ids))
        ).all()
        content_by_id = {row.id: row for row in content_rows}

    for row in legacy_rows:
        should_refresh_snapshot = (
            row.content_id is not None or not (row.context_snapshot or "").strip()
        )
        if not should_refresh_snapshot:
            continue
        snapshot = _build_context_snapshot(
            session_row=row,
            content_row=content_by_id.get(row.content_id) if row.content_id is not None else None,
        )
        session.execute(
            sa.update(chat_sessions)
            .where(chat_sessions.c.id == row.id)
            .values(context_snapshot=snapshot)
        )

    session.commit()


def downgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)

    chat_sessions = sa.table(
        "chat_sessions",
        sa.column("id", sa.Integer),
        sa.column("content_id", sa.Integer),
        sa.column("topic", sa.String),
        sa.column("session_type", sa.String),
    )

    session.execute(
        sa.update(chat_sessions)
        .where(
            chat_sessions.c.session_type == KNOWLEDGE_SESSION_TYPE,
            chat_sessions.c.content_id.is_(None),
        )
        .values(session_type="assistant_quick")
    )
    session.execute(
        sa.update(chat_sessions)
        .where(
            chat_sessions.c.session_type == KNOWLEDGE_SESSION_TYPE,
            chat_sessions.c.content_id.is_not(None),
            chat_sessions.c.topic.is_(None),
        )
        .values(session_type="article_brain")
    )
    session.execute(
        sa.update(chat_sessions)
        .where(
            chat_sessions.c.session_type == KNOWLEDGE_SESSION_TYPE,
            chat_sessions.c.content_id.is_not(None),
            chat_sessions.c.topic.is_not(None),
        )
        .values(session_type="topic")
    )
    session.commit()
