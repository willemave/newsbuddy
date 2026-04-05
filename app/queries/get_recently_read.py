"""Application query for recently-read content cards."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.api.common import ContentListResponse
from app.models.content_display import resolve_image_urls
from app.models.content_mapper import content_to_domain
from app.models.metadata import ContentType
from app.models.pagination import PaginationMetadata
from app.repositories.content_card_repository import get_recently_read, list_content_types
from app.routers.api.content_responses import build_content_summary_response
from app.utils.pagination import PaginationCursor

logger = get_logger(__name__)


def execute(db: Session, *, user_id: int, cursor: str | None, limit: int) -> ContentListResponse:
    """Return recently-read content list response."""
    last_id = None
    last_read_at = None
    if cursor:
        try:
            cursor_data = PaginationCursor.decode_cursor(cursor)
            last_id = cursor_data["last_id"]
            raw_last_read_at = cursor_data.get("last_read_at")
            if raw_last_read_at:
                last_read_at = datetime.fromisoformat(raw_last_read_at)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    rows = get_recently_read(
        db,
        user_id=user_id,
        last_id=last_id,
        last_read_at=last_read_at,
        limit=limit,
    )
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    contents = []
    for content, read_id, is_favorited, _read_at in rows:
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
                is_favorited=bool(is_favorited),
                image_url=image_url,
                thumbnail_url=thumbnail_url,
            )
        )

    next_cursor = None
    if has_more and rows:
        last_content, _read_id, _is_favorited, last_read_at_value = rows[-1]
        last_read_at_filter = last_read_at_value.isoformat() if last_read_at_value else None
        next_cursor = PaginationCursor.encode_cursor(
            last_id=last_content.id,
            last_created_at=last_content.created_at,
            filters={"last_read_at": last_read_at_filter},
        )

    return ContentListResponse(
        contents=contents,
        available_dates=[],
        content_types=[ContentType(value) for value in list_content_types()],
        meta=PaginationMetadata(
            next_cursor=next_cursor,
            has_more=has_more,
            page_size=len(contents),
            total=len(contents),
        ),
    )
