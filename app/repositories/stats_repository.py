"""Repository for user-scoped content statistics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import (
    Content,
    ContentKnowledgeSave,
    ContentReadStatus,
    ContentStatusEntry,
    NewsItem,
    ProcessingTask,
)
from app.repositories.content_repository import apply_visibility_filters, build_visibility_context
from app.services.news_feed import count_unread_news_items

settings = get_settings()


def _build_active_processing_filter(now_utc: datetime):
    active_task_exists = exists(
        select(ProcessingTask.id).where(
            ProcessingTask.content_id == Content.id,
            ProcessingTask.status.in_(
                [
                    ContentStatus.PENDING.value,
                    ContentStatus.PROCESSING.value,
                ]
            ),
        )
    )
    fresh_checkout = and_(
        Content.checked_out_by.is_not(None),
        Content.checked_out_at.is_not(None),
        Content.checked_out_at
        >= now_utc - timedelta(minutes=settings.checkout_timeout_minutes),
    )
    return or_(active_task_exists, fresh_checkout)


def get_unread_counts(db: Session, *, user_id: int) -> dict[str, int]:
    """Return unread counts by content type."""
    context = build_visibility_context(user_id)
    count_query = db.query(Content.content_type, func.count(Content.id))
    count_query = apply_visibility_filters(count_query, context)
    count_query = count_query.filter(Content.content_type != ContentType.NEWS.value)
    count_query = count_query.filter(~context.is_read).group_by(Content.content_type)
    results = count_query.all()

    counts = {"article": 0, "podcast": 0, "news": 0}
    for content_type, count in results:
        if content_type in counts:
            counts[content_type] = int(count or 0)

    counts["news"] = count_unread_news_items(db, user_id=user_id)
    return counts


def get_processing_count(db: Session, *, user_id: int) -> dict[str, int]:
    """Return processing counts for long-form and short-form content."""
    long_form_types = {ContentType.ARTICLE.value, ContentType.PODCAST.value}
    processing_statuses = {
        ContentStatus.NEW.value,
        ContentStatus.PENDING.value,
        ContentStatus.PROCESSING.value,
    }
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    active_processing_filter = _build_active_processing_filter(now_utc)

    base_query = (
        db.query(func.count(Content.id))
        .join(ContentStatusEntry, ContentStatusEntry.content_id == Content.id)
        .filter(ContentStatusEntry.user_id == user_id)
        .filter(ContentStatusEntry.status == "inbox")
        .filter(Content.status.in_(processing_statuses))
        .filter(active_processing_filter)
    )

    long_form_count = int(
        base_query.filter(
            or_(
                Content.content_type.in_(long_form_types),
                and_(Content.platform == "youtube", Content.content_type != ContentType.NEWS.value),
            )
        ).scalar()
        or 0
    )
    news_count = int(
        db.query(func.count(NewsItem.id))
        .filter(
            NewsItem.status.in_(
                [
                    "new",
                    "processing",
                ]
            )
        )
        .filter(
            or_(
                NewsItem.visibility_scope == "global",
                and_(
                    NewsItem.visibility_scope == "user",
                    NewsItem.owner_user_id == user_id,
                ),
            )
        )
        .scalar()
        or 0
    )

    return {
        "processing_count": long_form_count + news_count,
        "long_form_count": long_form_count,
        "news_count": news_count,
    }


def get_long_form_stats(db: Session, *, user_id: int) -> dict[str, int]:
    """Return long-form stats for the user."""
    long_form_types = {ContentType.ARTICLE.value, ContentType.PODCAST.value}
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    active_processing_filter = _build_active_processing_filter(now_utc)
    inbox_filter = (
        ContentStatusEntry.user_id == user_id,
        ContentStatusEntry.status == "inbox",
        or_(
            Content.content_type.in_(long_form_types),
            and_(Content.platform == "youtube", Content.content_type != ContentType.NEWS.value),
        ),
    )
    completed_filter = (
        Content.status == ContentStatus.COMPLETED.value,
        (Content.classification != "skip") | (Content.classification.is_(None)),
    )
    read_exists = exists(
        select(ContentReadStatus.id).where(
            ContentReadStatus.user_id == user_id,
            ContentReadStatus.content_id == Content.id,
        )
    )
    favorite_exists = exists(
        select(ContentKnowledgeSave.id).where(
            ContentKnowledgeSave.user_id == user_id,
            ContentKnowledgeSave.content_id == Content.id,
        )
    )
    processing_statuses = [
        ContentStatus.NEW.value,
        ContentStatus.PENDING.value,
        ContentStatus.PROCESSING.value,
    ]

    return {
        "total_count": int(
            db.query(func.count(Content.id))
            .join(ContentStatusEntry, ContentStatusEntry.content_id == Content.id)
            .filter(*inbox_filter)
            .filter(*completed_filter)
            .scalar()
            or 0
        ),
        "read_count": int(
            db.query(func.count(Content.id))
            .join(ContentStatusEntry, ContentStatusEntry.content_id == Content.id)
            .filter(*inbox_filter)
            .filter(*completed_filter)
            .filter(read_exists)
            .scalar()
            or 0
        ),
        "unread_count": int(
            db.query(func.count(Content.id))
            .join(ContentStatusEntry, ContentStatusEntry.content_id == Content.id)
            .filter(*inbox_filter)
            .filter(*completed_filter)
            .filter(~read_exists)
            .scalar()
            or 0
        ),
        "saved_to_knowledge_count": int(
            db.query(func.count(Content.id))
            .join(ContentStatusEntry, ContentStatusEntry.content_id == Content.id)
            .filter(*inbox_filter)
            .filter(*completed_filter)
            .filter(favorite_exists)
            .scalar()
            or 0
        ),
        "processing_count": int(
            db.query(func.count(Content.id))
            .join(ContentStatusEntry, ContentStatusEntry.content_id == Content.id)
            .filter(*inbox_filter)
            .filter(Content.status.in_(processing_statuses))
            .filter(active_processing_filter)
            .scalar()
            or 0
        ),
    }
