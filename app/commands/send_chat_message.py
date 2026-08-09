"""Application command for accepting chat messages and dispatching processing."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.models.api.chat import (
    MessageProcessingStatus as MessageProcessingStatusDto,
)
from app.models.api.chat import SendChatMessageRequest, SendMessageResponse
from app.models.db import ChatSession
from app.models.internal.assistant import AssistantScreenContext
from app.queries.chat_read_models import (
    build_processing_user_message,
    require_message_id,
    require_session_id,
    require_timestamp,
    require_writable_session,
    resolve_active_child_session,
)
from app.services.assistant_router import ASSISTANT_SESSION_TYPES
from app.services.chat_turn_queue import build_chat_turn_context, stage_queued_chat_turn

logger = get_logger(__name__)


def execute(
    db: Session,
    *,
    user_id: int,
    session_id: int,
    request: SendChatMessageRequest,
) -> SendMessageResponse:
    """Create a processing message and enqueue the correct chat turn worker."""
    session = require_writable_session(db, session_id=session_id, user_id=user_id)

    effective_session = session
    if session.council_mode:
        active_child_session = resolve_active_child_session(db, session)
        if active_child_session is None:
            raise HTTPException(status_code=400, detail="No active council branch selected")
        effective_session = (
            db.query(ChatSession)
            .filter(ChatSession.id == active_child_session.id)
            .with_for_update()
            .one()
        )
    effective_session_id = require_session_id(effective_session)
    parent_session_id = require_session_id(session)

    logger.info(
        "Chat message accepted",
        extra=build_log_extra(
            component="chat",
            operation="send_message",
            event_name="chat.turn",
            status="started",
            user_id=user_id,
            session_id=effective_session_id,
            context_data={"model": effective_session.llm_model},
        ),
    )

    kind = "article"
    source = "realtime"
    screen_context: AssistantScreenContext | None = None
    if session.council_mode:
        kind = "council"
        source = "council"
    elif effective_session.session_type == "deep_research":
        kind = "deep_research"
    elif effective_session.session_type in ASSISTANT_SESSION_TYPES:
        kind = "assistant"
        source = "assistant"
        screen_context = AssistantScreenContext(
            screen_type=effective_session.session_type,
            screen_title=effective_session.title,
            content_id=effective_session.content_id,
            news_item_id=effective_session.news_item_id,
        )

    turn_context = build_chat_turn_context(
        effective_session,
        visible_session_id=parent_session_id,
        user_prompt=request.message,
        kind=kind,
        source=source,
        screen_context=screen_context,
    )
    db_message = stage_queued_chat_turn(db, context=turn_context)
    message_id = require_message_id(db_message)
    message_created_at = require_timestamp(
        db_message.created_at,
        detail="Chat message missing created_at",
    )
    effective_session.last_message_at = message_created_at
    effective_session.updated_at = message_created_at
    if session.council_mode:
        session.last_message_at = effective_session.last_message_at
        session.updated_at = effective_session.updated_at
    db.commit()

    trimmed_msg = request.message.replace("\n", " ")[:100]
    if len(request.message) > 100:
        trimmed_msg = f"{trimmed_msg}..."
    logger.info(
        "[Chat:SEND] sid=%s mid=%s user=%s prompt='%s'",
        session_id,
        message_id,
        user_id,
        trimmed_msg,
    )

    user_message = build_processing_user_message(
        db_message=db_message,
        session_id=parent_session_id,
        content=request.message,
    )

    return SendMessageResponse(
        session_id=parent_session_id,
        user_message=user_message,
        message_id=message_id,
        status=MessageProcessingStatusDto.PROCESSING,
    )
