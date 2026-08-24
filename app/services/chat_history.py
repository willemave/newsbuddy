"""Bounded loading of complete persisted chat turns for model history."""

from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.contracts import MessageProcessingStatus
from app.models.db import ChatMessage
from app.services.chat_context_budget import (
    CHAT_HISTORY_MAX_TOKENS,
    estimate_tokens,
    parse_historical_message_row,
)

logger = get_logger(__name__)


def load_message_history(
    db: Session,
    session_id: int,
    *,
    exclude_message_id: int | None = None,
    completed_only: bool = True,
    max_tokens: int = CHAT_HISTORY_MAX_TOKENS,
) -> list[ModelMessage]:
    """Load newest complete persisted turns within the history token budget."""
    if max_tokens <= 0:
        return []

    query = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)
    if exclude_message_id is not None:
        query = query.filter(ChatMessage.id != exclude_message_id)
    if completed_only:
        query = query.filter(ChatMessage.status == MessageProcessingStatus.COMPLETED.value)
    rows = (
        query.order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(get_settings().chat_history_message_limit)
        .all()
    )

    newest_turns: list[list[ModelMessage]] = []
    used_tokens = 0
    for row in rows:
        try:
            if not isinstance(row.message_list, str):
                continue
            turn = parse_historical_message_row(row.message_list)
            turn_tokens = estimate_tokens(ModelMessagesTypeAdapter.dump_json(turn).decode("utf-8"))
            if used_tokens + turn_tokens > max_tokens:
                break
            newest_turns.append(turn)
            used_tokens += turn_tokens
        except Exception as exc:  # noqa: BLE001 - skip one malformed historical row
            logger.warning("Failed to deserialize chat message %s: %s", row.id, exc)

    return [message for turn in reversed(newest_turns) for message in turn]
