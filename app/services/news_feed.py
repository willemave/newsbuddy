"""Visible news-item feed queries, presenters, and read-state helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy import cast as sa_cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.constants import (
    AGGREGATOR_SCRAPER_TYPE,
    NEWS_FEED_VISIBLE_LIMIT,
)
from app.models.api.common import (
    ContentDetailResponse,
    ContentListResponse,
)
from app.models.contracts import (
    ContentType,
    NewsItemStatus,
    NewsItemVisibilityScope,
)
from app.models.pagination import PaginationMetadata
from app.models.schema import NewsItem, NewsItemReadStatus, UserScraperConfig
from app.queries.news_item_content_adapter import (
    present_news_item_detail,
    present_news_item_summary,
)
from app.utils.pagination import PaginationCursor

# Brutalist Report is the only aggregator that surfaces topic subscriptions; we
# narrow its global rows to the user's selected topics by inspecting
# ``raw_metadata.aggregator.topic`` in the JSON column.
_TOPIC_SCOPED_AGGREGATOR_KEYS: frozenset[str] = frozenset({"brutalist"})


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
    timestamp = item.published_at or item.processed_at or item.ingested_at or item.created_at
    if timestamp is not None:
        return timestamp
    return datetime.now(UTC).replace(tzinfo=None)


def _has_user_scoped_scraper_news(db: Session, *, user_id: int) -> bool:
    return (
        db.query(NewsItem.id)
        .filter(NewsItem.visibility_scope == NewsItemVisibilityScope.USER.value)
        .filter(NewsItem.owner_user_id == user_id)
        .filter(NewsItem.user_scraper_config_id.is_not(None))
        .filter(
            NewsItem.status.in_(
                [
                    NewsItemStatus.NEW.value,
                    NewsItemStatus.PROCESSING.value,
                    NewsItemStatus.READY.value,
                ]
            )
        )
        .first()
        is not None
    )


def _user_aggregator_subscriptions(db: Session, *, user_id: int) -> dict[str, list[str]]:
    """Return aggregator key → selected topics for the user's active subs.

    Only ``user_scraper_configs`` rows with ``scraper_type='aggregator'`` and
    ``is_active=True`` are returned. Topics default to an empty list for
    aggregators that don't expose topic selection.
    """
    rows = (
        db.query(UserScraperConfig.config)
        .filter(UserScraperConfig.user_id == user_id)
        .filter(UserScraperConfig.scraper_type == AGGREGATOR_SCRAPER_TYPE)
        .filter(UserScraperConfig.is_active.is_(True))
        .all()
    )
    selections: dict[str, list[str]] = {}
    for (config,) in rows:
        if not isinstance(config, dict):
            continue
        key = str(config.get("key") or "").strip().lower()
        if not key:
            continue
        topics_raw = config.get("topics") or []
        topics = [t.strip().lower() for t in topics_raw if isinstance(t, str) and t.strip()]
        selections[key] = topics
    return selections


def _aggregator_visibility_clause(selections: dict[str, list[str]]):
    """Build the GLOBAL-scope OR clause restricted to the user's aggregators."""
    if not selections:
        return None

    aggregator_topic_path = sa_cast(NewsItem.raw_metadata, JSONB)["aggregator"]["topic"].astext

    per_key_clauses = []
    for key, topics in selections.items():
        if key in _TOPIC_SCOPED_AGGREGATOR_KEYS and topics:
            per_key_clauses.append(
                and_(
                    NewsItem.platform == key,
                    aggregator_topic_path.in_(topics),
                )
            )
        else:
            per_key_clauses.append(NewsItem.platform == key)

    return or_(*per_key_clauses)


def build_visible_news_item_filter(db: Session, *, user_id: int):
    user_clause = and_(
        NewsItem.visibility_scope == NewsItemVisibilityScope.USER.value,
        NewsItem.owner_user_id == user_id,
    )
    selections = _user_aggregator_subscriptions(db, user_id=user_id)
    aggregator_clause = _aggregator_visibility_clause(selections)

    if aggregator_clause is not None:
        # User has explicit aggregator picks: GLOBAL rows must match a selected
        # aggregator (and topic, for Brutalist). Non-aggregator GLOBAL rows are
        # hidden — only items that came in through the user's own scrapers are
        # added on top.
        global_clause = and_(
            NewsItem.visibility_scope == NewsItemVisibilityScope.GLOBAL.value,
            aggregator_clause,
        )
        return or_(global_clause, user_clause)

    if _has_user_scoped_scraper_news(db, user_id=user_id):
        return user_clause

    # Backwards-compat fallback for users who haven't picked aggregators yet:
    # show legacy GLOBAL non-reddit rows alongside their user-scoped items.
    global_non_reddit_clause = and_(
        NewsItem.visibility_scope == NewsItemVisibilityScope.GLOBAL.value,
        or_(
            NewsItem.source_type.is_(None),
            NewsItem.source_type != "reddit",
        ),
    )
    return or_(global_non_reddit_clause, user_clause)


def _news_item_is_read_clause(*, user_id: int):
    return exists(
        select(NewsItemReadStatus.id).where(
            NewsItemReadStatus.user_id == user_id,
            NewsItemReadStatus.news_item_id == NewsItem.id,
        )
    )


def _visible_news_item_query(db: Session, *, user_id: int):
    """Return the user's visible representative news items, capped to N rows.

    The pipeline keeps ingesting and clustering everything; the cap just trims
    the user-facing feed to the most recent ``NEWS_FEED_VISIBLE_LIMIT`` rows so
    the iOS list doesn't grow without bound. The cap is applied as a subquery
    of ids so callers can chain extra filters/order-bys/cursor pagination on
    top without re-implementing the cap.
    """
    visibility_clause = build_visible_news_item_filter(db, user_id=user_id)
    sort_expr = _news_item_sort_timestamp_expr()
    recent_id_subq = (
        select(NewsItem.id)
        .where(NewsItem.status == NewsItemStatus.READY.value)
        .where(NewsItem.representative_news_item_id.is_(None))
        .where(visibility_clause)
        .order_by(sort_expr.desc(), NewsItem.id.desc())
        .limit(NEWS_FEED_VISIBLE_LIMIT)
        .subquery()
    )
    return db.query(NewsItem).filter(NewsItem.id.in_(select(recent_id_subq.c.id)))


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
        {_news_item_sort_timestamp(item).date().isoformat() for item, _row_is_read in rows},
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
            present_news_item_summary(
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
    return present_news_item_detail(
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
