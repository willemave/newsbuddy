"""Application commands for refreshing discussion payloads on demand."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.common import ContentDiscussionResponse
from app.models.schema import ContentDiscussion
from app.queries import get_content_discussion as get_content_discussion_query
from app.queries import get_news_item_discussion as get_news_item_discussion_query
from app.repositories.content_detail_repository import get_visible_content
from app.services import news_item_discussions
from app.services.discussion_fetcher import (
    fetch_and_store_discussion,
    fetch_and_store_news_item_discussion,
)
from app.services.news_feed import get_visible_news_item


def refresh_content_discussion(
    db: Session,
    *,
    user_id: int,
    content_id: int,
) -> ContentDiscussionResponse:
    """Refresh and return discussion payload for one visible content item."""
    content = get_visible_content(db, user_id=user_id, content_id=content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Content not found")

    fetch_and_store_discussion(db, content_id=content_id)
    discussion_row = (
        db.query(ContentDiscussion).filter(ContentDiscussion.content_id == content_id).first()
    )
    return get_content_discussion_query.build_response_for_content(
        content=content,
        discussion_row=discussion_row,
    )


def refresh_news_item_discussion(
    db: Session,
    *,
    user_id: int,
    news_item_id: int,
) -> ContentDiscussionResponse:
    """Refresh and return discussion payload for one visible news item."""
    item = get_visible_news_item(
        db,
        user_id=user_id,
        news_item_id=news_item_id,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="News item not found")

    if news_item_discussions.is_supported_news_item_discussion(item):
        news_item_discussions.refresh_news_item_discussion(
            db,
            news_item_id=news_item_id,
        )
    elif item.legacy_content_id is not None:
        fetch_and_store_discussion(db, content_id=item.legacy_content_id)
    else:
        fetch_and_store_news_item_discussion(db, news_item_id=news_item_id)

    return get_news_item_discussion_query.build_response_for_news_item(db, item=item)
