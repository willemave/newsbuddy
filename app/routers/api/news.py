"""News-item feed and conversion endpoints."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.commands import refresh_content_discussion as refresh_content_discussion_command
from app.commands.convert_news_to_article import (
    convert_article_url_to_content,
    ensure_article_saved_to_knowledge,
)
from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user, require_user_id
from app.models.api.common import (
    BulkMarkReadRequest,
    ContentDetailResponse,
    ContentDiscussionResponse,
    ContentListResponse,
)
from app.models.api.news import ConvertNewsItemResponse
from app.models.user import User
from app.queries import get_news_item_discussion as get_news_item_discussion_query
from app.services.news_feed import (
    bulk_mark_news_items_read,
    get_visible_news_item,
    get_visible_news_item_detail,
    list_visible_news_items,
)
from app.utils.news_titles import get_news_article_title
from app.utils.url_utils import is_http_url, normalize_http_url

router = APIRouter(tags=["news"], responses={404: {"description": "Not found"}})


@router.get("/items", response_model=ContentListResponse, summary="List visible news items")
def list_news_items(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    read_filter: Annotated[
        str,
        Query(pattern="^(all|read|unread)$", description="Filter by read status"),
    ] = "unread",
    cursor: Annotated[str | None, Query(description="Opaque cursor token")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ContentListResponse:
    """Return the visible representative news feed for the current user."""
    user_id = require_user_id(current_user)
    return list_visible_news_items(
        db,
        user_id=user_id,
        read_filter=read_filter,
        cursor=cursor,
        limit=limit,
    )


@router.post("/items/mark-read", summary="Mark visible news items as read")
def mark_news_items_read(
    payload: BulkMarkReadRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Mark the given visible representative news items as read."""
    user_id = require_user_id(current_user)
    return bulk_mark_news_items_read(
        db,
        user_id=user_id,
        news_item_ids=payload.content_ids,
    )


@router.get(
    "/items/{news_item_id}",
    response_model=ContentDetailResponse,
    summary="Get one news item",
)
def get_news_item(
    news_item_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ContentDetailResponse:
    """Return one visible representative news item."""
    item = get_visible_news_item_detail(
        db,
        user_id=require_user_id(current_user),
        news_item_id=news_item_id,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="News item not found")
    return item


@router.get(
    "/items/{news_item_id}/discussion",
    response_model=ContentDiscussionResponse,
    summary="Get one news item discussion",
)
def get_news_item_discussion(
    news_item_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ContentDiscussionResponse:
    """Return discussion payload for one visible representative news item."""
    return get_news_item_discussion_query.execute(
        db,
        user_id=require_user_id(current_user),
        news_item_id=news_item_id,
    )


@router.post(
    "/items/{news_item_id}/discussion/refresh",
    response_model=ContentDiscussionResponse,
    summary="Refresh one news item discussion",
)
def refresh_news_item_discussion(
    news_item_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ContentDiscussionResponse:
    """Refresh discussion payload for one visible representative news item."""
    return refresh_content_discussion_command.refresh_news_item_discussion(
        db,
        user_id=require_user_id(current_user),
        news_item_id=news_item_id,
    )


@router.post(
    "/items/{news_item_id}/convert-to-article",
    response_model=ConvertNewsItemResponse,
    summary="Convert one news item into article content",
)
def convert_news_item_to_article(
    news_item_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConvertNewsItemResponse:
    """Convert one representative news item into saved article content."""
    user_id = require_user_id(current_user)
    item = get_visible_news_item(db, user_id=user_id, news_item_id=news_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="News item not found")

    article_url = normalize_http_url(item.article_url or item.canonical_story_url)
    if not is_http_url(article_url):
        raise HTTPException(status_code=400, detail="No article URL found for news item")
    canonical_article_url = cast(str, article_url)

    article, already_exists = convert_article_url_to_content(
        db,
        article_url=canonical_article_url,
        title=get_news_article_title(item.raw_metadata) or item.article_title,
        source=item.article_domain,
    )
    if item.id is None or article.id is None:
        raise HTTPException(status_code=500, detail="Converted content is missing required ids")
    ensure_article_saved_to_knowledge(db, user_id=user_id, content_id=article.id)

    return ConvertNewsItemResponse(
        news_item_id=item.id,
        new_content_id=article.id,
        already_exists=already_exists,
        message=(
            "Article already exists in system"
            if already_exists
            else "Article created and queued for processing"
        ),
    )
