"""Helpers for starting daily-digest dig-deeper chats."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.schema import ChatMessage, ChatSession, DailyNewsDigest
from app.services.chat_agent import create_processing_message
from app.services.daily_news_digest import (
    DailyDigestSourceItem,
    resolve_daily_digest_bullet_details,
)
from app.services.llm_models import DEFAULT_MODEL, DEFAULT_PROVIDER

DAILY_DIGEST_DIG_DEEPER_PROMPT = (
    "Dig deeper into these digest bullets. For each bullet, explain what happened, "
    "why it matters, and how it connects to the rest of the day. "
    "If context is missing, say so plainly."
)
DAILY_DIGEST_BULLET_DIG_DEEPER_PROMPT = (
    "Dig deeper into this digest bullet. Explain what happened, why it matters, "
    "and how the linked sources and comments support it. "
    "If evidence is missing or incomplete, say so plainly."
)


def build_daily_digest_context_snapshot(digest: DailyNewsDigest) -> str | None:
    """Build a bullets-only context snapshot for a daily digest chat.

    Args:
        digest: Daily digest row to snapshot.

    Returns:
        Plain-text bullets block or ``None`` when no usable bullets exist.
    """
    raw_points = digest.key_points if isinstance(digest.key_points, list) else []
    points = [str(point).strip() for point in raw_points if str(point).strip()]
    if not points:
        return None

    lines = ["Digest bullets:"]
    lines.extend(f"- {point}" for point in points)
    return "\n".join(lines)


def build_daily_digest_bullet_context_snapshot(
    digest: DailyNewsDigest,
    *,
    bullet_index: int,
    source_items_by_content_id: dict[int, DailyDigestSourceItem],
) -> tuple[str, str]:
    """Build a bullet-scoped context snapshot for one digest chat session."""
    bullet_details = resolve_daily_digest_bullet_details(
        digest,
        source_items_by_content_id=source_items_by_content_id,
    )
    if bullet_index < 0 or bullet_index >= len(bullet_details):
        raise IndexError("Daily digest bullet not found")

    bullet = bullet_details[bullet_index]
    lines = [
        "Selected digest bullet:",
        f"- {bullet.text}",
    ]

    citations = [
        source_items_by_content_id[content_id]
        for content_id in bullet.source_content_ids
        if content_id in source_items_by_content_id
    ]
    if citations:
        lines.append("")
        lines.append("Linked sources:")
        for citation in citations:
            label = citation.source_label or "Source"
            if citation.source_url:
                lines.append(f"- {label}: {citation.title} ({citation.source_url})")
            else:
                lines.append(f"- {label}: {citation.title}")

    if bullet.comment_quotes:
        lines.append("")
        lines.append("Stored discussion comments:")
        lines.extend(f"- {quote}" for quote in bullet.comment_quotes)

    return "\n".join(lines), bullet.text


def start_daily_digest_chat(
    db: Session,
    *,
    digest: DailyNewsDigest,
    user_id: int,
) -> tuple[ChatSession, ChatMessage, str]:
    """Create a fresh daily-digest chat session and seed the first prompt.

    Args:
        db: Database session.
        digest: User-owned daily digest row.
        user_id: Authenticated user creating the chat.

    Returns:
        Tuple of created session, processing message, and seeded prompt text.

    Raises:
        ValueError: If the digest has no usable key points.
    """
    context_snapshot = build_daily_digest_context_snapshot(digest)
    if context_snapshot is None:
        raise ValueError("Daily digest dig-deeper requires summary bullets")

    session = ChatSession(
        user_id=user_id,
        content_id=None,
        title=digest.title,
        session_type="daily_digest_brain",
        topic=None,
        context_snapshot=context_snapshot,
        llm_provider=DEFAULT_PROVIDER,
        llm_model=DEFAULT_MODEL,
        created_at=datetime.now(UTC),
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    message = create_processing_message(db, session.id, DAILY_DIGEST_DIG_DEEPER_PROMPT)
    return session, message, DAILY_DIGEST_DIG_DEEPER_PROMPT


def start_daily_digest_bullet_chat(
    db: Session,
    *,
    digest: DailyNewsDigest,
    bullet_index: int,
    user_id: int,
    source_items_by_content_id: dict[int, DailyDigestSourceItem],
) -> tuple[ChatSession, ChatMessage, str]:
    """Create a fresh daily-digest chat session focused on one selected bullet."""
    context_snapshot, bullet_text = build_daily_digest_bullet_context_snapshot(
        digest,
        bullet_index=bullet_index,
        source_items_by_content_id=source_items_by_content_id,
    )

    session = ChatSession(
        user_id=user_id,
        content_id=None,
        title=digest.title,
        session_type="daily_digest_brain",
        topic=bullet_text,
        context_snapshot=context_snapshot,
        llm_provider=DEFAULT_PROVIDER,
        llm_model=DEFAULT_MODEL,
        created_at=datetime.now(UTC),
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    message = create_processing_message(db, session.id, DAILY_DIGEST_BULLET_DIG_DEEPER_PROMPT)
    return session, message, DAILY_DIGEST_BULLET_DIG_DEEPER_PROMPT
