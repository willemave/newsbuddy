"""Application command for one-off feed backfill from a content item."""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.models.api.common import DownloadMoreResponse
from app.models.contracts import ContentType
from app.models.internal.feed_backfill import FeedBackfillRequest
from app.models.schema import Content
from app.repositories.content_repository import build_visibility_context
from app.services.feed_backfill import (
    backfill_feed_for_config,
    resolve_feed_config_for_content,
)


async def execute(
    db: Session,
    *,
    user_id: int,
    content_id: int,
    count: int,
) -> DownloadMoreResponse:
    """Backfill additional feed items for the series that produced a content item."""
    context = build_visibility_context(user_id)
    row = (
        db.query(Content, context.is_in_inbox.label("is_in_inbox"))
        .filter(Content.id == content_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Content not found")

    content, is_in_inbox = row
    if content.content_type not in (ContentType.ARTICLE.value, ContentType.PODCAST.value):
        raise HTTPException(status_code=400, detail="Content is not long-form")
    if not is_in_inbox:
        raise HTTPException(status_code=403, detail="Content not accessible")

    config = resolve_feed_config_for_content(db, user_id, content)
    if not config:
        raise HTTPException(status_code=400, detail="Feed config not found for content")

    try:
        result = await run_in_threadpool(
            backfill_feed_for_config,
            FeedBackfillRequest(
                user_id=user_id,
                config_id=config.id,
                count=count,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return DownloadMoreResponse(
        status="completed",
        requested_count=count,
        base_limit=result.base_limit,
        target_limit=result.target_limit,
        scraped=result.scraped,
        saved=result.saved,
        duplicates=result.duplicates,
        errors=result.errors,
    )
