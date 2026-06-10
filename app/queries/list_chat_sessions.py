"""Application query for listing chat sessions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.api.chat import ChatSessionListResponse, ChatSessionSummaryDto
from app.models.api.pagination import PaginationMetadata
from app.queries.chat_read_models import (
    build_session_summaries,
    list_visible_chat_sessions,
    require_session_id,
    require_timestamp,
)
from app.utils.pagination import PaginationCursor


def execute(
    db: Session,
    *,
    user_id: int,
    content_id: int | None,
    news_item_id: int | None,
    limit: int,
) -> list[ChatSessionSummaryDto]:
    """Return visible chat session summaries for a user."""
    sessions = list_visible_chat_sessions(
        db,
        user_id=user_id,
        content_id=content_id,
        news_item_id=news_item_id,
        limit=limit,
    )
    return build_session_summaries(db, user_id=user_id, sessions=sessions)


def execute_page(
    db: Session,
    *,
    user_id: int,
    content_id: int | None,
    news_item_id: int | None,
    cursor: str | None,
    limit: int,
) -> ChatSessionListResponse:
    """Return one cursor-paginated page of visible chat sessions."""
    rows = list_visible_chat_sessions(
        db,
        user_id=user_id,
        content_id=content_id,
        news_item_id=news_item_id,
        limit=limit,
        cursor=cursor,
        overfetch=True,
    )
    has_more = len(rows) > limit
    sessions = rows[:limit] if has_more else rows
    next_cursor = None
    if has_more and sessions:
        last_session = sessions[-1]
        next_cursor = PaginationCursor.encode_cursor(
            last_id=require_session_id(last_session),
            last_created_at=require_timestamp(
                last_session.last_message_at or last_session.created_at,
                detail="Chat session missing activity timestamp",
            ),
            filters={"content_id": content_id, "news_item_id": news_item_id},
        )
    return ChatSessionListResponse(
        sessions=build_session_summaries(db, user_id=user_id, sessions=sessions),
        meta=PaginationMetadata(
            next_cursor=next_cursor,
            has_more=has_more,
            page_size=len(sessions),
            total=len(sessions),
        ),
    )
