from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.settings import Settings, get_settings
from app.models.contracts import ContentClassification, ContentStatus, ContentType, NewsItemStatus
from app.models.db import (
    Content,
    ContentReadStatus,
    ContentStatusEntry,
    NewsItem,
    NewsItemReadStatus,
)
from app.services.briefing.refresh import enqueue_ready_source, is_briefing_enabled_for_user
from app.services.news_feed import get_visible_news_item


def enqueue_content_for_briefing_if_ready(
    db: Session,
    *,
    content_id: int,
    settings: Settings | None = None,
) -> int:
    settings = settings or get_settings()
    content = db.query(Content).filter(Content.id == content_id).first()
    if content is None:
        return 0
    if content.status != ContentStatus.COMPLETED.value:
        return 0
    if content.classification == ContentClassification.SKIP.value:
        return 0
    if content.content_type not in {ContentType.ARTICLE.value, ContentType.PODCAST.value}:
        return 0

    lens_key = "podcasts" if content.content_type == ContentType.PODCAST.value else "articles"
    user_rows = (
        db.query(ContentStatusEntry.user_id)
        .filter(ContentStatusEntry.content_id == content_id)
        .filter(ContentStatusEntry.status == "inbox")
        .all()
    )
    enqueued = 0
    for (user_id,) in user_rows:
        if not isinstance(user_id, int):
            continue
        if not is_briefing_enabled_for_user(user_id, settings=settings):
            continue
        is_read = db.execute(
            select(
                exists().where(
                    ContentReadStatus.user_id == user_id,
                    ContentReadStatus.content_id == content_id,
                )
            )
        ).scalar()
        if is_read:
            continue
        enqueued += int(
            enqueue_ready_source(
                db,
                user_id=user_id,
                source_kind="content",
                source_id=content_id,
                lens_key=lens_key,
                settings=settings,
            )
        )
    return enqueued


def enqueue_news_item_for_briefing_if_ready(
    db: Session,
    *,
    news_item_id: int,
    settings: Settings | None = None,
) -> int:
    settings = settings or get_settings()
    item = db.query(NewsItem).filter(NewsItem.id == news_item_id).first()
    if item is None or item.status != NewsItemStatus.READY.value:
        return 0

    enqueued = 0
    for raw_user_id in settings.briefing_enabled_user_ids:
        user_id = int(raw_user_id)
        if get_visible_news_item(db, user_id=user_id, news_item_id=news_item_id) is None:
            continue
        is_read = db.execute(
            select(
                exists().where(
                    NewsItemReadStatus.user_id == user_id,
                    NewsItemReadStatus.news_item_id == news_item_id,
                )
            )
        ).scalar()
        if is_read:
            continue
        enqueued += int(
            enqueue_ready_source(
                db,
                user_id=user_id,
                source_kind="news",
                source_id=news_item_id,
                settings=settings,
            )
        )
    return enqueued
