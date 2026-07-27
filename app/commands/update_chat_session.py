"""Application command for updating chat session settings."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.models.api.chat import ChatSessionSummaryDto, UpdateChatSessionRequest
from app.models.db import ChatSession
from app.queries.chat_read_models import (
    require_session_id,
    resolve_session_article_presentation,
    session_to_summary,
)
from app.services.llm_models import is_deep_research_provider, resolve_model

logger = get_logger(__name__)


def execute(
    db: Session,
    *,
    user_id: int,
    session_id: int,
    request: UpdateChatSessionRequest,
) -> ChatSessionSummaryDto:
    """Update a chat session's provider or model settings."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")

    if request.llm_provider is not None:
        if is_deep_research_provider(request.llm_provider):
            raise HTTPException(
                status_code=400,
                detail="Deep research must be started as a dedicated deep research session",
            )
        provider, model_spec = resolve_model(request.llm_provider, request.llm_model_hint)
        session.llm_provider = provider
        session.llm_model = model_spec
        session.updated_at = datetime.now(UTC)

        logger.info(
            "Chat session provider changed",
            extra=build_log_extra(
                component="chat",
                operation="update_session",
                event_name="chat.session_provider_changed",
                status="completed",
                user_id=user_id,
                session_id=require_session_id(session),
                context_data={"model": model_spec},
            ),
        )

    db.commit()
    db.refresh(session)
    return session_to_summary(
        session,
        resolve_session_article_presentation(db, session),
    )
