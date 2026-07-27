"""Application command for screen-aware assistant chat turns."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.models.api.chat import AssistantTurnRequest, AssistantTurnResponse
from app.models.api.chat import MessageProcessingStatus as MessageProcessingStatusDto
from app.models.db import ChatSession, Content
from app.models.internal.assistant import AssistantScreenContext
from app.queries.chat_read_models import (
    build_processing_user_message,
    build_session_summaries,
    require_message_id,
    require_session_id,
    require_timestamp,
    resolve_article_title,
    resolve_news_item_title,
)
from app.services.assistant_router import (
    build_screen_context_snapshot,
    create_assistant_session,
    process_assistant_turn_async,
)
from app.services.chat_agent import create_processing_message
from app.services.news_feed import get_visible_news_item

logger = get_logger(__name__)


def execute(
    db: Session,
    *,
    user_id: int,
    request: AssistantTurnRequest,
    background_tasks: BackgroundTasks,
    process_assistant_turn: Callable[..., Awaitable[None]] = process_assistant_turn_async,
) -> AssistantTurnResponse:
    """Create or continue an assistant-driven chat turn with screen context."""
    screen_context = request.screen_context
    if screen_context.news_item_id is not None and not get_visible_news_item(
        db,
        user_id=user_id,
        news_item_id=screen_context.news_item_id,
    ):
        raise HTTPException(status_code=404, detail="News item not found")

    if request.session_id is not None:
        session = db.query(ChatSession).filter(ChatSession.id == request.session_id).first()
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.user_id != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to access this session")
        _refresh_session_context(
            db=db,
            session=session,
            user_id=user_id,
            screen_context=screen_context,
        )
    else:
        context_snapshot = build_screen_context_snapshot(
            db,
            user_id=user_id,
            screen_context=screen_context,
        )
        session = create_assistant_session(
            db,
            user_id=user_id,
            context_snapshot=context_snapshot,
            screen_context=screen_context,
            initial_message=request.message,
        )

    session_id = require_session_id(session)
    logger.info(
        "Assistant turn accepted",
        extra=build_log_extra(
            component="assistant_turn",
            operation="create_turn",
            event_name="assistant.turn",
            status="started",
            user_id=user_id,
            session_id=session_id,
            content_id=screen_context.content_id,
            context_data={
                "model": session.llm_model,
                "screen_type": screen_context.screen_type,
            },
        ),
    )

    db_message = create_processing_message(db, session_id, request.message)
    message_id = require_message_id(db_message)
    message_created_at = require_timestamp(
        db_message.created_at,
        detail="Chat message missing created_at",
    )
    session.last_message_at = message_created_at
    session.updated_at = message_created_at
    db.commit()
    db.refresh(session)

    background_tasks.add_task(
        process_assistant_turn,
        session_id,
        message_id,
        request.message,
        screen_context=screen_context,
    )
    return AssistantTurnResponse(
        session=build_session_summaries(db, user_id=user_id, sessions=[session])[0],
        user_message=build_processing_user_message(
            db_message=db_message,
            session_id=session_id,
            content=request.message,
        ),
        message_id=message_id,
        status=MessageProcessingStatusDto.PROCESSING,
    )


def _refresh_session_context(
    *,
    db: Session,
    session: ChatSession,
    user_id: int,
    screen_context: AssistantScreenContext,
) -> None:
    session.context_snapshot = build_screen_context_snapshot(
        db,
        user_id=user_id,
        screen_context=screen_context,
    )
    session.content_id = screen_context.content_id
    session.news_item_id = screen_context.news_item_id
    session.topic = screen_context.selected_topic

    title = screen_context.screen_title or session.title or "Knowledge Chat"
    if screen_context.content_id is not None:
        content = db.query(Content).filter(Content.id == screen_context.content_id).first()
        if content is not None:
            title = resolve_article_title(content) or title
    elif screen_context.news_item_id is not None:
        item = get_visible_news_item(
            db,
            user_id=user_id,
            news_item_id=screen_context.news_item_id,
        )
        if item is not None:
            title = resolve_news_item_title(item)
    session.title = title[:500]
    session.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(session)
