"""Application command for accepting chat messages and dispatching processing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import BackgroundTasks, HTTPException
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
    resolve_active_child_session,
)
from app.services.assistant_router import ASSISTANT_SESSION_TYPES, process_assistant_turn_async
from app.services.chat_agent import create_processing_message, process_message_async

logger = get_logger(__name__)


def execute(
    db: Session,
    *,
    user_id: int,
    session_id: int,
    request: SendChatMessageRequest,
    background_tasks: BackgroundTasks,
    process_message: Callable[..., Awaitable[None]] = process_message_async,
    process_assistant_turn: Callable[..., Awaitable[None]] = process_assistant_turn_async,
) -> SendMessageResponse:
    """Create a processing message and enqueue the correct chat turn worker."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")

    effective_session = session
    if session.council_mode:
        active_child_session = resolve_active_child_session(db, session)
        if active_child_session is None:
            raise HTTPException(status_code=400, detail="No active council branch selected")
        effective_session = active_child_session
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

    db_message = create_processing_message(db, effective_session_id, request.message)
    message_id = require_message_id(db_message)
    effective_session.last_message_at = datetime.now(UTC)
    effective_session.updated_at = datetime.now(UTC)
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

    _dispatch_processing(
        background_tasks,
        effective_session=effective_session,
        effective_session_id=effective_session_id,
        message_id=message_id,
        message=request.message,
        source="council" if session.council_mode else None,
        process_message=process_message,
        process_assistant_turn=process_assistant_turn,
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


def _dispatch_processing(
    background_tasks: BackgroundTasks,
    *,
    effective_session: ChatSession,
    effective_session_id: int,
    message_id: int,
    message: str,
    source: str | None,
    process_message: Callable[..., Awaitable[None]],
    process_assistant_turn: Callable[..., Awaitable[None]],
) -> None:
    if source == "council":
        background_tasks.add_task(
            process_message,
            effective_session_id,
            message_id,
            message,
            source="council",
        )
    elif effective_session.session_type == "deep_research":
        from app.services.deep_research import process_deep_research_message

        background_tasks.add_task(
            process_deep_research_message,
            effective_session_id,
            message_id,
            message,
        )
    elif effective_session.session_type in ASSISTANT_SESSION_TYPES:
        background_tasks.add_task(
            process_assistant_turn,
            effective_session_id,
            message_id,
            message,
            screen_context=AssistantScreenContext(
                screen_type=effective_session.session_type,
                screen_title=effective_session.title,
                content_id=effective_session.content_id,
                news_item_id=effective_session.news_item_id,
            ),
        )
    else:
        background_tasks.add_task(process_message, effective_session_id, message_id, message)
