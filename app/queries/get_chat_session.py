"""Application query for one chat session detail."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.chat import ChatSessionDetailDto
from app.models.db import ChatSession
from app.queries.chat_read_models import (
    extract_messages_for_display,
    require_session_id,
    resolve_active_child_session,
    resolve_session_article_presentation,
    session_to_summary,
)


def execute(
    db: Session,
    *,
    user_id: int,
    session_id: int,
) -> ChatSessionDetailDto:
    """Return a chat session and display messages for the owning user."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")

    article_presentation = resolve_session_article_presentation(db, session)

    messages = extract_messages_for_display(db, session_id)
    if session.council_mode:
        active_child_session = resolve_active_child_session(db, session)
        if active_child_session is not None:
            branch_messages = extract_messages_for_display(
                db,
                require_session_id(active_child_session),
                session_id_override=require_session_id(session),
                min_message_id_exclusive=active_child_session.branch_start_message_id,
            )
            messages.extend(branch_messages)

    session_summary = session_to_summary(
        session,
        article_presentation,
    )
    return ChatSessionDetailDto(session=session_summary, messages=messages)
