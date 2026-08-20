"""Application query for async chat message status."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.chat import ChatMessageDto, MessageStatusResponse
from app.models.api.chat import (
    MessageProcessingStatus as MessageProcessingStatusDto,
)
from app.models.contracts import ChatMessageRole
from app.models.db import ChatMessage, ChatSession
from app.queries.chat_read_models import (
    apply_feed_subscription_state,
    build_async_assistant_display_id,
    load_render_metadata,
    require_session_id,
    require_timestamp,
    resolve_message_status,
)
from app.services.feed_subscription import load_active_feed_urls


def execute(
    db: Session,
    *,
    user_id: int,
    message_id: int,
) -> MessageStatusResponse:
    """Return the processing status and completed assistant message, if any."""
    from pydantic_ai.messages import (
        ModelMessagesTypeAdapter,
        ModelResponse,
        TextPart,
    )

    db_message = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()

    if not db_message:
        raise HTTPException(status_code=404, detail="Message not found")

    session = db.query(ChatSession).filter(ChatSession.id == db_message.session_id).first()
    if not session or session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this message")

    status = resolve_message_status(db_message)
    if status == MessageProcessingStatusDto.PROCESSING:
        partial_assistant_message = None
        if isinstance(db_message.partial_text, str) and db_message.partial_text.strip():
            partial_assistant_message = ChatMessageDto(
                id=build_async_assistant_display_id(message_id),
                source_message_id=message_id,
                session_id=session.parent_session_id or require_session_id(session),
                role=ChatMessageRole.ASSISTANT,
                content=db_message.partial_text,
                timestamp=require_timestamp(
                    db_message.stream_updated_at or db_message.created_at,
                    detail="Partial chat message missing timestamp",
                ),
                status=MessageProcessingStatusDto.PROCESSING,
            )
        return MessageStatusResponse(
            message_id=message_id,
            status=status,
            assistant_message=None,
            partial_assistant_message=partial_assistant_message,
            stream_generation=db_message.stream_generation,
            stream_revision=db_message.stream_revision,
            error=None,
        )

    if status == MessageProcessingStatusDto.FAILED:
        return MessageStatusResponse(
            message_id=message_id,
            status=status,
            assistant_message=None,
            partial_assistant_message=None,
            stream_generation=None,
            stream_revision=None,
            error=db_message.error,
        )

    try:
        message_list_json = db_message.message_list
        if not isinstance(message_list_json, str):
            raise HTTPException(status_code=500, detail="Message payload missing")
        msg_list = ModelMessagesTypeAdapter.validate_json(message_list_json)
        render_metadata = load_render_metadata(db_message)
        feed_options = render_metadata.feed_options if render_metadata else []
        if feed_options:
            feed_options = apply_feed_subscription_state(
                feed_options,
                active_feed_urls=load_active_feed_urls(db, user_id=user_id),
            )

        assistant_content = None
        for model_msg in reversed(msg_list):
            if isinstance(model_msg, ModelResponse):
                for part in model_msg.parts:
                    if isinstance(part, TextPart) and part.content:
                        assistant_content = part.content
                        break
                if assistant_content:
                    break

        if not assistant_content:
            raise HTTPException(status_code=500, detail="Assistant response missing")

        assistant_message = ChatMessageDto(
            id=build_async_assistant_display_id(message_id),
            source_message_id=message_id,
            session_id=session.parent_session_id or require_session_id(session),
            role=ChatMessageRole.ASSISTANT,
            content=assistant_content,
            timestamp=require_timestamp(
                db_message.created_at,
                detail="Chat message missing created_at",
            ),
            status=MessageProcessingStatusDto.COMPLETED,
            feed_options=feed_options,
            council_candidates=render_metadata.council_candidates if render_metadata else [],
            active_council_child_session_id=(
                render_metadata.active_council_child_session_id if render_metadata else None
            ),
        )

        return MessageStatusResponse(
            message_id=message_id,
            status=status,
            assistant_message=assistant_message,
            partial_assistant_message=None,
            stream_generation=None,
            stream_revision=None,
            error=None,
        )

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Failed to parse message") from exc
