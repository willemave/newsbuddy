"""Helpers for starting daily-digest dig-deeper chats."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.schema import ChatMessage, ChatSession, DailyNewsDigest
from app.services.chat_agent import create_processing_message
from app.services.llm_models import DEFAULT_MODEL, DEFAULT_PROVIDER

DAILY_DIGEST_DIG_DEEPER_PROMPT = (
    "Dig deeper into these digest bullets. For each bullet, explain what happened, "
    "why it matters, and how it connects to the rest of the day. "
    "If context is missing, say so plainly."
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
