"""Acceptance-time helpers for durable chat turns."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.contracts import TaskType
from app.models.db import ChatMessage, ChatSession
from app.models.internal.assistant import AssistantScreenContext
from app.models.internal.chat_turn import (
    ChatTurnProcessingContext,
)
from app.services.chat_agent import create_processing_message
from app.services.gateways.task_queue_gateway import get_task_queue_gateway
from app.services.llm_models import DEFAULT_MODEL, resolve_model_provider
from app.services.queue import TaskEnqueueRequest


def build_chat_turn_context(
    session: ChatSession,
    *,
    visible_session_id: int,
    user_prompt: str,
    kind: str,
    source: str,
    screen_context: AssistantScreenContext | None = None,
) -> ChatTurnProcessingContext:
    """Freeze every mutable session field consumed by a queued turn."""
    if session.id is None or session.user_id is None:
        raise ValueError("Chat session must be persisted before queuing a turn")
    model = session.llm_model or DEFAULT_MODEL
    provider = session.llm_provider or resolve_model_provider(model)
    return ChatTurnProcessingContext.model_validate(
        {
            "kind": kind,
            "user_prompt": user_prompt,
            "source": source,
            "session": {
                "user_id": int(session.user_id),
                "effective_session_id": int(session.id),
                "visible_session_id": visible_session_id,
                "model": model,
                "provider": provider,
                "title": session.title,
                "session_type": session.session_type,
                "content_id": session.content_id,
                "news_item_id": session.news_item_id,
                "parent_session_id": session.parent_session_id,
                "topic": session.topic,
                "context_snapshot": session.context_snapshot,
                "is_hidden_from_history": bool(session.is_hidden_from_history),
                "council_persona_id": session.council_persona_id,
                "council_persona_name": session.council_persona_name,
                "council_persona_prompt": session.council_persona_prompt,
            },
            "screen_context": screen_context,
        }
    )


def stage_queued_chat_turn(
    db: Session,
    *,
    context: ChatTurnProcessingContext,
) -> ChatMessage:
    """Stage a processing message and its queue task in the caller transaction."""
    session_snapshot = context.session
    db_message = create_processing_message(
        db,
        session_snapshot.effective_session_id,
        context.user_prompt,
        processing_context=context.model_dump(mode="json"),
        commit=False,
    )
    if db_message.id is None:
        raise ValueError("Processing message was not persisted")
    message_id = int(db_message.id)
    get_task_queue_gateway().enqueue_many_in_session(
        db,
        [
            TaskEnqueueRequest(
                task_type=TaskType.CHAT_TURN,
                payload={
                    "user_id": session_snapshot.user_id,
                    "session_id": session_snapshot.effective_session_id,
                    "message_id": message_id,
                },
                dedupe_key=f"chat-turn:message:{message_id}",
                owner_user_id=session_snapshot.user_id,
            )
        ],
    )
    return db_message
