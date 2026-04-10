"""Visible news-item feed queries, presenters, and read-state helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.api.common import (
    ContentDetailResponse,
    ContentListResponse,
    ContentSummaryResponse,
)
from app.models.contracts import (
    ContentClassification,
    ContentStatus,
    ContentType,
    NewsItemStatus,
    NewsItemVisibilityScope,
)
from app.models.pagination import PaginationMetadata
from app.models.schema import NewsItem, NewsItemReadStatus
from app.utils.pagination import PaginationCursor
from app.utils.title_utils import resolve_display_title, resolve_title_candidate
from app.utils.url_utils import normalize_http_url


def _read_status_insert_for_dialect(db: Session):
    """Return the canonical insert builder for news-item read-status writes."""
    del db
    return postgresql_insert(NewsItemReadStatus)


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _news_item_sort_timestamp_expr():
    return func.coalesce(
        NewsItem.published_at,
        NewsItem.processed_at,
        NewsItem.ingested_at,
        NewsItem.created_at,
    )


def _news_item_sort_timestamp(item: NewsItem) -> datetime:
    return item.published_at or item.processed_at or item.ingested_at or item.created_at


def _resolve_item_url(item: NewsItem) -> str:
    for candidate in (
        item.article_url,
        item.canonical_story_url,
        item.discussion_url,
        item.canonical_item_url,
    ):
        normalized = normalize_http_url(candidate) if candidate else None
        if normalized:
            return normalized
    return f"https://newsly.invalid/news-items/{item.id}"


def _cluster_metadata(item: NewsItem) -> dict[str, Any]:
    raw_metadata = dict(item.raw_metadata or {})
    cluster = raw_metadata.get("cluster")
    return cluster if isinstance(cluster, dict) else {}


def _top_comment(item: NewsItem) -> dict[str, str] | None:
    raw_top_comment = dict(item.raw_metadata or {}).get("top_comment")
    if not isinstance(raw_top_comment, dict):
        return None
    author = str(raw_top_comment.get("author") or "unknown").strip() or "unknown"
    text = str(raw_top_comment.get("text") or "").strip()
    if not text:
        return None
    return {"author": author, "text": text}


def _comment_count(item: NewsItem) -> int | None:
    """Return the best available discussion count for a news item."""
    metadata = dict(item.raw_metadata or {})
    aggregator = metadata.get("aggregator")
    aggregator_metadata = aggregator.get("metadata") if isinstance(aggregator, dict) else None

    for raw in (
        metadata.get("comment_count"),
        (
            aggregator_metadata.get("comments_count")
            if isinstance(aggregator_metadata, dict)
            else None
        ),
    ):
        if raw is None:
            continue
        try:
            return max(int(raw), 0)
        except (TypeError, ValueError):
            continue

    if item.cluster_size > 1:
        return item.cluster_size - 1
    return None


def _content_status(item: NewsItem) -> ContentStatus:
    if item.status == "failed":
        return ContentStatus.FAILED
    if item.status == "processing":
        return ContentStatus.PROCESSING
    if item.status == "new":
        return ContentStatus.NEW
    return ContentStatus.COMPLETED


def _content_classification(item: NewsItem) -> ContentClassification | None:
    raw_summary = dict(item.raw_metadata or {}).get("summary")
    if not isinstance(raw_summary, dict):
        return None
    classification = raw_summary.get("classification")
    if classification in {ContentClassification.TO_READ.value, ContentClassification.SKIP.value}:
        return ContentClassification(classification)
    return None


def _news_item_display_title(item: NewsItem) -> str:
    """Resolve a news-item title that avoids placeholder source labels."""
    return resolve_display_title(
        item.summary_title,
        item.article_title,
        summary_text=item.summary_text,
        fallback=f"News item {item.id}",
    )


def _present_summary(
    item: NewsItem,
    *,
    is_read: bool,
) -> ContentSummaryResponse:
    cluster = _cluster_metadata(item)
    discussion_snippets = cluster.get("discussion_snippets")
    top_comment = _top_comment(item)
    if top_comment is None and isinstance(discussion_snippets, list) and discussion_snippets:
        top_comment = {"author": "Related", "text": str(discussion_snippets[0]).strip()}
    display_title = _news_item_display_title(item)

    return ContentSummaryResponse(
        id=item.id,
        content_type=ContentType.NEWS,
        url=_resolve_item_url(item),
        source_url=item.canonical_item_url or item.discussion_url,
        discussion_url=item.discussion_url,
        title=display_title,
        source=item.source_label,
        platform=item.platform,
        status=_content_status(item),
        short_summary=item.summary_text,
        created_at=(item.ingested_at or item.created_at).isoformat(),
        processed_at=item.processed_at.isoformat() if item.processed_at else None,
        classification=_content_classification(item),
        publication_date=item.published_at.isoformat() if item.published_at else None,
        is_read=is_read,
        is_saved_to_knowledge=False,
        news_article_url=item.article_url or item.canonical_story_url,
        news_discussion_url=item.discussion_url or item.canonical_item_url,
        news_key_points=list(item.summary_key_points or []) or None,
        news_summary=item.summary_text,
        user_status=None,
        image_url=None,
        thumbnail_url=None,
        primary_topic=None,
        top_comment=top_comment,
        comment_count=_comment_count(item),
    )


def _present_detail(
    item: NewsItem,
    *,
    is_read: bool,
) -> ContentDetailResponse:
    metadata = dict(item.raw_metadata or {})
    display_title = _news_item_display_title(item)
    article = metadata.get("article")
    if not isinstance(article, dict):
        article = {}
    article.setdefault("url", item.article_url or item.canonical_story_url)
    article["title"] = resolve_display_title(
        article.get("title"),
        display_title,
        summary_text=item.summary_text,
        fallback=display_title,
    )
    article.setdefault("source_domain", item.article_domain)
    metadata["article"] = article

    summary = metadata.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    raw_summary_text = summary.get("summary")
    summary_text = raw_summary_text if isinstance(raw_summary_text, str) else item.summary_text
    summary_title = resolve_title_candidate(
        summary.get("title"),
        display_title,
        summary_text=summary_text,
    )
    article_url = item.article_url or item.canonical_story_url
    summary_key_points = list(item.summary_key_points or [])
    if summary_title:
        summary["title"] = summary_title
    if article_url and not summary.get("article_url"):
        summary["article_url"] = article_url
    if summary_key_points and not summary.get("key_points"):
        summary["key_points"] = summary_key_points
    if item.summary_text and not summary.get("summary"):
        summary["summary"] = item.summary_text
    metadata["summary"] = summary

    metadata.setdefault("discussion_url", item.discussion_url)
    metadata.setdefault("cluster", _cluster_metadata(item))

    return ContentDetailResponse(
        id=item.id,
        content_type=ContentType.NEWS,
        url=_resolve_item_url(item),
        source_url=item.canonical_item_url or item.discussion_url,
        discussion_url=item.discussion_url,
        title=display_title,
        display_title=display_title,
        source=item.source_label,
        status=_content_status(item),
        error_message=None,
        retry_count=0,
        metadata=metadata,
        created_at=(item.ingested_at or item.created_at).isoformat(),
        updated_at=item.updated_at.isoformat() if item.updated_at else None,
        processed_at=item.processed_at.isoformat() if item.processed_at else None,
        checked_out_by=None,
        checked_out_at=None,
        publication_date=item.published_at.isoformat() if item.published_at else None,
        is_read=is_read,
        is_saved_to_knowledge=False,
        summary=item.summary_text,
        short_summary=item.summary_text,
        summary_kind=None,
        summary_version=None,
        structured_summary=None,
        bullet_points=[],
        quotes=[],
        topics=[],
        full_markdown=None,
        news_article_url=item.article_url or item.canonical_story_url,
        news_discussion_url=item.discussion_url or item.canonical_item_url,
        news_key_points=list(item.summary_key_points or []) or None,
        news_summary=item.summary_text,
        image_url=None,
        thumbnail_url=None,
        detected_feed=None,
        can_subscribe=False,
    )


def _visible_news_item_filter(user_id: int):
    return or_(
        NewsItem.visibility_scope == NewsItemVisibilityScope.GLOBAL.value,
        and_(
            NewsItem.visibility_scope == NewsItemVisibilityScope.USER.value,
            NewsItem.owner_user_id == user_id,
        ),
    )


def _news_item_is_read_clause(*, user_id: int):
    return exists(
        select(NewsItemReadStatus.id).where(
            NewsItemReadStatus.user_id == user_id,
            NewsItemReadStatus.news_item_id == NewsItem.id,
        )
    )


def _visible_news_item_query(db: Session, *, user_id: int):
    return (
        db.query(NewsItem)
        .filter(NewsItem.status == NewsItemStatus.READY.value)
        .filter(NewsItem.representative_news_item_id.is_(None))
        .filter(_visible_news_item_filter(user_id))
    )


def list_visible_news_items(
    db: Session,
    *,
    user_id: int,
    read_filter: str,
    cursor: str | None,
    limit: int,
) -> ContentListResponse:
    """Return the visible representative news feed for one user."""
    last_id = None
    last_sort_timestamp = None
    if cursor:
        cursor_data = PaginationCursor.decode_cursor(cursor)
        last_id = cursor_data["last_id"]
        last_sort_timestamp = cursor_data["last_created_at"]

    is_read = _news_item_is_read_clause(user_id=user_id)
    sort_expr = _news_item_sort_timestamp_expr()
    query = _visible_news_item_query(db, user_id=user_id).add_columns(is_read.label("is_read"))
    if read_filter == "unread":
        query = query.filter(~is_read)
    elif read_filter == "read":
        query = query.filter(is_read)

    if last_sort_timestamp is not None and last_id is not None:
        query = query.filter(
            or_(
                sort_expr < last_sort_timestamp,
                and_(sort_expr == last_sort_timestamp, NewsItem.id < last_id),
            )
        )

    rows = query.order_by(sort_expr.desc(), NewsItem.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    available_dates = sorted(
        {
            _coerce_utc(_news_item_sort_timestamp(item)).date().isoformat()
            for item, _row_is_read in rows
        },
        reverse=True,
    )
    next_cursor = None
    if has_more and rows:
        last_item = rows[-1][0]
        next_cursor = PaginationCursor.encode_cursor(
            last_id=last_item.id,
            last_created_at=_news_item_sort_timestamp(last_item),
            filters={"read_filter": read_filter},
        )

    return ContentListResponse(
        contents=[
            _present_summary(
                item,
                is_read=bool(row_is_read),
            )
            for item, row_is_read in rows
        ],
        available_dates=available_dates,
        content_types=[ContentType.NEWS],
        meta=PaginationMetadata(
            next_cursor=next_cursor,
            has_more=has_more,
            page_size=len(rows),
            total=len(rows),
        ),
    )


def get_visible_news_item_detail(
    db: Session,
    *,
    user_id: int,
    news_item_id: int,
) -> ContentDetailResponse | None:
    """Return one visible representative news item detail response."""
    is_read = _news_item_is_read_clause(user_id=user_id)
    row = (
        _visible_news_item_query(db, user_id=user_id)
        .add_columns(is_read.label("is_read"))
        .filter(NewsItem.id == news_item_id)
        .first()
    )
    if row is None:
        return None
    item, row_is_read = row
    return _present_detail(
        item,
        is_read=bool(row_is_read),
    )


def get_visible_news_item(db: Session, *, user_id: int, news_item_id: int) -> NewsItem | None:
    """Return a visible representative news item row or ``None`` when inaccessible."""
    return _visible_news_item_query(db, user_id=user_id).filter(NewsItem.id == news_item_id).first()


def bulk_mark_news_items_read(
    db: Session,
    *,
    user_id: int,
    news_item_ids: list[int],
) -> dict[str, Any]:
    """Mark visible representative news items as read for one user."""
    requested_ids = list(news_item_ids)
    visible_ids = {
        row.id
        for row in _visible_news_item_query(db, user_id=user_id)
        .with_entities(NewsItem.id)
        .filter(NewsItem.id.in_(news_item_ids))
        .all()
    }
    if not visible_ids:
        return {
            "status": "success",
            "marked_count": 0,
            "failed_ids": requested_ids,
            "total_requested": len(requested_ids),
        }

    try:
        timestamp = datetime.now(UTC).replace(tzinfo=None)
        stmt = (
            _read_status_insert_for_dialect(db)
            .values(
                [
                    {
                        "user_id": user_id,
                        "news_item_id": news_item_id,
                        "read_at": timestamp,
                        "created_at": timestamp,
                    }
                    for news_item_id in sorted(visible_ids)
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[
                    NewsItemReadStatus.user_id,
                    NewsItemReadStatus.news_item_id,
                ]
            )
            .returning(NewsItemReadStatus.news_item_id)
        )
        inserted_ids = db.execute(stmt).scalars().all()
        db.commit()
        marked_count = len(inserted_ids)
    except OperationalError:
        db.rollback()
        return {
            "status": "success",
            "marked_count": 0,
            "failed_ids": sorted(visible_ids),
            "total_requested": len(requested_ids),
        }
    return {
        "status": "success",
        "marked_count": marked_count,
        "failed_ids": sorted(set(requested_ids) - visible_ids),
        "total_requested": len(requested_ids),
    }


def count_unread_news_items(db: Session, *, user_id: int) -> int:
    """Return the unread count for visible representative news items."""
    is_read = _news_item_is_read_clause(user_id=user_id)
    return int(
        _visible_news_item_query(db, user_id=user_id)
        .with_entities(func.count(NewsItem.id))
        .filter(~is_read)
        .scalar()
        or 0
    )
