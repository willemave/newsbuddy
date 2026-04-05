"""Application query for favorited content cards."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.api.common import ContentListResponse
from app.models.content_display import resolve_image_urls
from app.models.content_mapper import content_to_domain
from app.models.metadata import ContentType
from app.models.pagination import PaginationMetadata
from app.repositories.content_card_repository import get_favorites, list_content_types
from app.routers.api.content_responses import build_content_summary_response
from app.utils.pagination import PaginationCursor

logger = get_logger(__name__)


def execute(
    db: Session,
    *,
    user_id: int,
    cursor: str | None,
    limit: int,
) -> ContentListResponse:
    """Return favorited content list response."""
    last_id = None
    last_sort_timestamp = None
    if cursor:
        try:
            cursor_data = PaginationCursor.decode_cursor(cursor)
            last_id = cursor_data["last_id"]
            last_sort_timestamp = cursor_data["last_created_at"]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    rows = get_favorites(
        db,
        user_id=user_id,
        last_id=last_id,
        last_sort_timestamp=last_sort_timestamp,
        limit=limit,
    )
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    contents = []
    for content, read_id, _favorite_id in rows:
        try:
            domain_content = content_to_domain(content)
        except Exception:
            logger.exception(
                "Skipping invalid content row in favorites",
                extra={
                    "component": "get_favorites",
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
                is_favorited=True,
                image_url=image_url,
                thumbnail_url=thumbnail_url,
            )
        )

    next_cursor = None
    if has_more and rows:
        last_item = rows[-1][0]
        next_cursor = PaginationCursor.encode_cursor(
            last_id=last_item.id,
            last_created_at=last_item.created_at,
            filters={},
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
