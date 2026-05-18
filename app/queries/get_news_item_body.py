"""Application query for canonical short-form news article body access."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.content import ContentBodyResponse
from app.queries.get_content_body import _truncate_body_text
from app.services.content_bodies import ContentBodyVariant
from app.services.news_article_bodies import get_news_item_article_body_resolver
from app.services.news_feed import get_visible_news_item


def execute(
    db: Session,
    *,
    user_id: int,
    news_item_id: int,
    variant: str,
) -> ContentBodyResponse:
    """Return canonical article body text for a visible news item."""
    news_item = get_visible_news_item(db, user_id=user_id, news_item_id=news_item_id)
    if news_item is None:
        raise HTTPException(status_code=404, detail="News item not found")

    resolved = get_news_item_article_body_resolver().resolve(
        db,
        news_item=news_item,
        variant=ContentBodyVariant(variant),
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="News item body not found")

    return ContentBodyResponse(
        content_id=news_item_id,
        variant=resolved.variant.value,
        kind="article",
        format=resolved.format.value,
        text=_truncate_body_text(resolved.text),
        updated_at=resolved.updated_at.isoformat() if resolved.updated_at else None,
    )
