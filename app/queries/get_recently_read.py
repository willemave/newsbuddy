"""Application query for recently-read content cards."""

from __future__ import annotations

from datetime import date as date_type

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.api.content import ContentListResponse
from app.models.api.pagination import PaginationMetadata
from app.models.contracts import ContentType
from app.models.domain.content_display import resolve_image_urls
from app.models.domain.content_mapper import content_to_domain
from app.presenters.content_responses import build_content_summary_response
from app.repositories.content_card_repository import (
    get_recently_read,
    list_content_types,
    list_recently_read_dates,
)
from app.utils.pagination import PaginationCursor

logger = get_logger(__name__)


def _parse_filter_date(raw_date: str | None) -> date_type | None:
    if raw_date is None:
        return None
    try:
        return date_type.fromisoformat(raw_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="date must be a valid YYYY-MM-DD date",
        ) from exc


def execute(
    db: Session,
    *,
    user_id: int,
    content_type: list[str] | None,
    date: str | None,
    cursor: str | None,
    limit: int,
) -> ContentListResponse:
    """Return recently-read content list response."""
    filter_date = _parse_filter_date(date)
    last_id = None
    last_read_at = None
    if cursor:
        try:
            cursor_data = PaginationCursor.decode_cursor(cursor)
            current_filters = {
                "content_type": content_type,
                "date": date,
            }
            if not PaginationCursor.validate_cursor(cursor_data, current_filters):
                raise HTTPException(
                    status_code=400,
                    detail="Cursor invalid: filters changed. Start a new pagination.",
                )
            last_id = cursor_data.last_id
            last_read_at = cursor_data.last_created_at
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    rows = get_recently_read(
        db,
        user_id=user_id,
        content_types=content_type,
        read_date=filter_date,
        last_id=last_id,
        last_read_at=last_read_at,
        limit=limit,
    )
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    contents = []
    for content, read_id, is_saved_to_knowledge, _read_at in rows:
        try:
            domain_content = content_to_domain(content)
        except Exception:
            logger.exception(
                "Skipping invalid content row in recently_read",
                extra={
                    "component": "get_recently_read",
                    "operation": "content_to_domain",
                    "item_id": content.id,
                },
            )
            continue
        image_url, thumbnail_url = resolve_image_urls(domain_content)
        contents.append(
            build_content_summary_response(
                content=content,
                domain_content=domain_content,
                is_read=bool(read_id),
                is_saved_to_knowledge=bool(is_saved_to_knowledge),
                image_url=image_url,
                thumbnail_url=thumbnail_url,
            )
        )

    next_cursor = None
    if has_more and rows:
        last_content, _read_id, _is_saved_to_knowledge, last_read_at_value = rows[-1]
        next_cursor = PaginationCursor.encode_cursor(
            last_id=last_content.id,
            last_created_at=last_read_at_value or last_content.created_at,
            filters={
                "content_type": content_type,
                "date": date,
            },
        )

    return ContentListResponse(
        contents=contents,
        available_dates=list_recently_read_dates(
            db,
            user_id=user_id,
            content_types=content_type,
        ),
        content_types=[ContentType(value) for value in list_content_types()],
        meta=PaginationMetadata(
            next_cursor=next_cursor,
            has_more=has_more,
            page_size=len(contents),
            total=len(contents),
        ),
    )
