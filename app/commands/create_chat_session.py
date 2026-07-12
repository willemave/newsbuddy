"""Application command for creating chat sessions."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.models.api.chat import CreateChatSessionRequest, CreateChatSessionResponse
from app.models.db import ChatSession, Content
from app.models.internal.assistant import AssistantScreenContext
from app.queries.chat_read_models import (
    extract_short_summary,
    news_item_article_metadata,
    require_session_id,
    resolve_article_title,
    resolve_session_article_presentation,
    session_to_summary,
)
from app.services.assistant_router import KNOWLEDGE_SESSION_TYPE, build_screen_context_snapshot
from app.services.llm_models import is_deep_research_provider, resolve_model
from app.services.news_feed import get_visible_news_item
from app.services.personal_markdown_library import sync_personal_markdown_for_content
from app.utils.title_utils import derive_chat_session_title

logger = get_logger(__name__)


def execute(
    db: Session,
    *,
    user_id: int,
    request: CreateChatSessionRequest,
) -> CreateChatSessionResponse:
    """Create a new chat session."""
    if request.content_id is not None and request.news_item_id is not None:
        raise HTTPException(status_code=400, detail="Use either content_id or news_item_id")

    provider, model_spec = resolve_model(request.llm_provider, request.llm_model_hint)
    if is_deep_research_provider(request.llm_provider):
        session_type = "deep_research"
    else:
        session_type = KNOWLEDGE_SESSION_TYPE

    article_title = None
    article_url = None
    article_summary = None
    article_source = None
    context_snapshot: str | None = None
    if request.content_id:
        content = db.query(Content).filter(Content.id == request.content_id).first()
        if not content:
            raise HTTPException(status_code=404, detail="Content not found")
        article_title = resolve_article_title(content)
        article_url = content.url
        article_summary = extract_short_summary(content)
        article_source = content.source
        context_snapshot = _build_knowledge_context_snapshot(
            db,
            user_id=user_id,
            request=request,
            content_id=request.content_id,
            news_item_id=None,
        )
    elif request.news_item_id:
        news_item = get_visible_news_item(
            db,
            user_id=user_id,
            news_item_id=request.news_item_id,
        )
        if not news_item:
            raise HTTPException(status_code=404, detail="News item not found")
        article_title, article_url, article_summary, article_source = news_item_article_metadata(
            news_item
        )
        context_snapshot = _build_knowledge_context_snapshot(
            db,
            user_id=user_id,
            request=request,
            content_id=None,
            news_item_id=request.news_item_id,
        )
    elif session_type == KNOWLEDGE_SESSION_TYPE:
        context_snapshot = _build_knowledge_context_snapshot(
            db,
            user_id=user_id,
            request=request,
            content_id=None,
            news_item_id=None,
        )

    if request.topic and article_title:
        title = f"{article_title} - {request.topic}"
    elif article_title:
        title = article_title
    elif request.topic:
        title = request.topic
    elif derived_title := derive_chat_session_title(request.initial_message):
        title = derived_title
    else:
        title = "New Chat"

    session = ChatSession(
        user_id=user_id,
        content_id=request.content_id,
        news_item_id=request.news_item_id,
        title=title,
        session_type=session_type,
        topic=request.topic,
        context_snapshot=context_snapshot,
        llm_model=model_spec,
        llm_provider=provider,
        created_at=datetime.now(UTC),
    )

    db.add(session)
    db.commit()
    db.refresh(session)
    session_row_id = require_session_id(session)

    if request.content_id:
        try:
            sync_personal_markdown_for_content(
                db,
                user_id=user_id,
                content_id=request.content_id,
            )
        except Exception:
            logger.exception(
                "Failed to sync personal markdown after chat session creation",
                extra=build_log_extra(
                    component="chat",
                    operation="create_session",
                    event_name="chat.session.personal_markdown",
                    status="degraded",
                    user_id=user_id,
                    session_id=session_row_id,
                    content_id=request.content_id,
                ),
            )

    logger.info(
        "Chat session created",
        extra=build_log_extra(
            component="chat",
            operation="create_session",
            event_name="chat.session",
            status="completed",
            user_id=user_id,
            session_id=session_row_id,
            content_id=request.content_id,
            context_data={"model": model_spec, "session_type": session_type},
        ),
    )

    article_presentation = resolve_session_article_presentation(db, session)
    session_summary = session_to_summary(
        session,
        article_presentation,
    )
    return CreateChatSessionResponse(session=session_summary)


def _build_knowledge_context_snapshot(
    db: Session,
    *,
    user_id: int,
    request: CreateChatSessionRequest,
    content_id: int | None,
    news_item_id: int | None,
) -> str | None:
    return build_screen_context_snapshot(
        db,
        user_id=user_id,
        screen_context=AssistantScreenContext(
            screen_type=KNOWLEDGE_SESSION_TYPE,
            screen_title="Knowledge",
            content_id=content_id,
            news_item_id=news_item_id,
            selected_topic=request.topic,
            note=request.initial_message[:500] if request.initial_message else None,
        ),
    )
