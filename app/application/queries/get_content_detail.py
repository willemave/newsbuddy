"""Application query for content detail."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.presenters.content_presenter import (
    build_content_detail_response,
    build_domain_content,
    can_subscribe_for_feed,
)
from app.repositories.content_detail_repository import get_content_detail
from app.services.feed_subscription import can_subscribe_to_feed
from app.services.news_feed import get_visible_news_item_detail


def execute(db: Session, *, user_id: int, content_id: int):
    """Return content detail response."""
    row = get_content_detail(db, user_id=user_id, content_id=content_id)
    if not row:
        news_item_detail = get_visible_news_item_detail(
            db,
            user_id=user_id,
            news_item_id=content_id,
        )
        if news_item_detail is not None:
            return news_item_detail
        raise HTTPException(status_code=404, detail="Content not found")

    content, is_read, is_favorited, body_available, body_format = row
    try:
        domain_content = build_domain_content(content)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process content metadata: {exc!s}",
        ) from exc

    detected_feed_data = (domain_content.metadata or {}).get("detected_feed")
    can_subscribe = False
    if can_subscribe_for_feed(domain_content, detected_feed_data):
        can_subscribe = can_subscribe_to_feed(db, user_id, detected_feed_data)

    return build_content_detail_response(
        content=content,
        domain_content=domain_content,
        is_read=bool(is_read),
        is_favorited=bool(is_favorited),
        detected_feed_data=detected_feed_data,
        can_subscribe=can_subscribe,
        body_available=bool(body_available),
        body_kind="transcript" if content.content_type == "podcast" else "article",
        body_format=str(body_format) if body_format else None,
    )
