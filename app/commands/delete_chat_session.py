"""Application command for archiving chat sessions."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.models.db import ChatSession
from app.queries.chat_read_models import require_session_id

logger = get_logger(__name__)


def execute(db: Session, *, user_id: int, session_id: int) -> None:
    """Soft-delete a chat session for the current user."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")

    if not session.is_archived:
        now = datetime.now(UTC)
        session.is_archived = True
        session.updated_at = now
        if session.council_mode:
            (
                db.query(ChatSession)
                .filter(ChatSession.parent_session_id == session.id)
                .update(
                    {
                        ChatSession.is_archived: True,
                        ChatSession.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
        db.commit()

    logger.info(
        "Chat session archived",
        extra=build_log_extra(
            component="chat",
            operation="delete_session",
            event_name="chat.session_deleted",
            status="completed",
            user_id=user_id,
            session_id=require_session_id(session),
        ),
    )
