"""Projection-oriented repository for content card queries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models.contracts import ContentStatus
from app.models.metadata import ContentType
from app.models.schema import (
    Content,
    ContentKnowledgeSave,
    ContentReadStatus,
    ContentStatusEntry,
)
from app.repositories.content_feed_query import (
    apply_sort_timestamp_cursor,
    build_user_feed_query,
    content_sort_timestamp_expr,
)

AVAILABLE_DATES_LOOKBACK_DAYS = 120
LONG_FORM_CONTENT_TYPES = {ContentType.ARTICLE.value, ContentType.PODCAST.value}


def _filtered_content_types(content_types: list[str] | None) -> list[str]:
    return [content_type for content_type in (content_types or []) if content_type != "all"]


def _is_long_form_only_request(content_types: list[str] | None) -> bool:
    filtered_types = _filtered_content_types(content_types)
    return bool(filtered_types) and set(filtered_types).issubset(LONG_FORM_CONTENT_TYPES)


def _build_long_form_page_query(
    db: Session,
    *,
    user_id: int,
    content_types: list[str] | None,
    date: str | None,
    read_filter: str,
    last_id: int | None,
    last_sort_timestamp: datetime | None,
):
    sort_expr = content_sort_timestamp_expr()
    read_exists = exists(
        select(ContentReadStatus.id).where(
            ContentReadStatus.user_id == user_id,
            ContentReadStatus.content_id == Content.id,
        )
    )

    query = (
        db.query(Content.id.label("content_id"), sort_expr.label("sort_timestamp"))
        .join(
            ContentStatusEntry,
            and_(
                ContentStatusEntry.content_id == Content.id,
                ContentStatusEntry.user_id == user_id,
                ContentStatusEntry.status == "inbox",
            ),
        )
        .filter(Content.status == ContentStatus.COMPLETED.value)
        .filter((Content.classification != "skip") | (Content.classification.is_(None)))
    )

    filtered_types = _filtered_content_types(content_types)
    if filtered_types:
        query = query.filter(Content.content_type.in_(filtered_types))

    if date:
        filter_date = datetime.strptime(date, "%Y-%m-%d").date()  # noqa: DTZ007
        start_dt = datetime.combine(filter_date, datetime.min.time())  # noqa: DTZ001
        end_dt = start_dt + timedelta(days=1)
        query = query.filter(sort_expr >= start_dt, sort_expr < end_dt)

    if read_filter == "unread":
        query = query.filter(~read_exists)
    elif read_filter == "read":
        query = query.filter(read_exists)

    query = apply_sort_timestamp_cursor(query, last_sort_timestamp, last_id, sort_expr=sort_expr)
    return query, sort_expr


def _list_long_form_contents(
    db: Session,
    *,
    user_id: int,
    content_types: list[str] | None,
    date: str | None,
    read_filter: str,
    last_id: int | None,
    last_sort_timestamp: datetime | None,
    limit: int,
    include_available_dates: bool,
):
    available_dates: list[str] = []
    page_query, sort_expr = _build_long_form_page_query(
        db,
        user_id=user_id,
        content_types=content_types,
        date=date,
        read_filter=read_filter,
        last_id=last_id,
        last_sort_timestamp=last_sort_timestamp,
    )

    if include_available_dates and last_id is None and last_sort_timestamp is None:
        lookback_start = datetime.now(UTC) - timedelta(days=AVAILABLE_DATES_LOOKBACK_DAYS)
        available_dates_query = page_query.with_entities(func.date(sort_expr).label("date"))
        available_dates_query = available_dates_query.filter(sort_expr >= lookback_start)
        available_dates_query = (
            available_dates_query.distinct().order_by(func.date(sort_expr).desc()).limit(90)
        )
        for row in available_dates_query.all():
            if not row.date:
                continue
            available_dates.append(
                row.date if isinstance(row.date, str) else row.date.strftime("%Y-%m-%d")
            )

    page_rows = page_query.order_by(sort_expr.desc(), Content.id.desc()).limit(limit + 1).all()
    ordered_content_ids = [int(row.content_id) for row in page_rows]
    if not ordered_content_ids:
        return [], available_dates

    detail_rows = (
        db.query(
            Content,
            ContentReadStatus.id.label("is_read"),
            ContentKnowledgeSave.id.label("is_saved_to_knowledge"),
        )
        .outerjoin(
            ContentReadStatus,
            and_(
                ContentReadStatus.content_id == Content.id,
                ContentReadStatus.user_id == user_id,
            ),
        )
        .outerjoin(
            ContentKnowledgeSave,
            and_(
                ContentKnowledgeSave.content_id == Content.id,
                ContentKnowledgeSave.user_id == user_id,
            ),
        )
        .filter(Content.id.in_(ordered_content_ids))
        .all()
    )
    rows_by_id = {
        int(content.id): (content, is_read, is_saved)
        for content, is_read, is_saved in detail_rows
        if content.id is not None
    }
    ordered_rows = [
        rows_by_id[content_id] for content_id in ordered_content_ids if content_id in rows_by_id
    ]
    return ordered_rows, available_dates


def list_contents(
    db: Session,
    *,
    user_id: int,
    content_types: list[str] | None,
    date: str | None,
    read_filter: str,
    last_id: int | None,
    last_sort_timestamp: datetime | None,
    limit: int,
    include_available_dates: bool = True,
):
    """Return visible inbox card rows and available dates."""
    if _is_long_form_only_request(content_types):
        return _list_long_form_contents(
            db,
            user_id=user_id,
            content_types=content_types,
            date=date,
            read_filter=read_filter,
            last_id=last_id,
            last_sort_timestamp=last_sort_timestamp,
            limit=limit,
            include_available_dates=include_available_dates,
        )

    available_dates: list[str] = []
    sort_expr = content_sort_timestamp_expr()
    if include_available_dates and last_id is None and last_sort_timestamp is None:
        lookback_start = datetime.now(UTC) - timedelta(days=AVAILABLE_DATES_LOOKBACK_DAYS)
        available_dates_query = build_user_feed_query(db, user_id, mode="inbox").with_entities(
            func.date(sort_expr).label("date")
        )
        available_dates_query = available_dates_query.filter(sort_expr >= lookback_start)
        available_dates_query = (
            available_dates_query.distinct().order_by(func.date(sort_expr).desc()).limit(90)
        )
        for row in available_dates_query.all():
            if not row.date:
                continue
            available_dates.append(
                row.date if isinstance(row.date, str) else row.date.strftime("%Y-%m-%d")
            )

    query = build_user_feed_query(db, user_id, mode="inbox")
    filtered_types = _filtered_content_types(content_types)
    if filtered_types:
        query = query.filter(Content.content_type.in_(filtered_types))

    if date:
        filter_date = datetime.strptime(date, "%Y-%m-%d").date()  # noqa: DTZ007
        start_dt = datetime.combine(filter_date, datetime.min.time())  # noqa: DTZ001
        end_dt = start_dt + timedelta(days=1)
        query = query.filter(sort_expr >= start_dt, sort_expr < end_dt)

    if read_filter == "unread":
        query = query.filter(ContentReadStatus.id.is_(None))
    elif read_filter == "read":
        query = query.filter(ContentReadStatus.id.is_not(None))

    query = apply_sort_timestamp_cursor(query, last_sort_timestamp, last_id, sort_expr=sort_expr)
    rows = query.order_by(sort_expr.desc(), Content.id.desc()).limit(limit + 1).all()
    return rows, available_dates


def get_knowledge_library_entries(
    db: Session,
    *,
    user_id: int,
    last_id: int | None,
    last_sort_timestamp: datetime | None,
    limit: int,
):
    """Return knowledge-library card rows."""
    query = build_user_feed_query(db, user_id, mode="knowledge_library")
    query = apply_sort_timestamp_cursor(query, last_sort_timestamp, last_id)
    return query.order_by(Content.created_at.desc(), Content.id.desc()).limit(limit + 1).all()


def get_recently_read(
    db: Session,
    *,
    user_id: int,
    last_id: int | None,
    last_read_at: datetime | None,
    limit: int,
):
    """Return recently-read card rows ordered by read timestamp."""
    query = build_user_feed_query(db, user_id, mode="recently_read").add_columns(
        ContentReadStatus.read_at.label("read_at")
    )
    if last_id and last_read_at:
        query = query.filter(
            or_(
                ContentReadStatus.read_at < last_read_at,
                (ContentReadStatus.read_at == last_read_at) & (Content.id < last_id),
            )
        )
    return (
        query.order_by(ContentReadStatus.read_at.desc(), Content.id.desc()).limit(limit + 1).all()
    )


def list_content_types() -> list[str]:
    """Return public content type filters for card endpoints."""
    return [content_type.value for content_type in ContentType]
