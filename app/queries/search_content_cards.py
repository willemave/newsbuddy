"""Application query for content search cards."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.api.common import ContentListResponse
from app.models.content_display import is_ready_for_list, resolve_image_urls
from app.models.content_mapper import content_to_domain
from app.models.metadata import ContentType
from app.models.pagination import PaginationMetadata
from app.repositories.content_card_repository import list_content_types, search_contents
from app.repositories.search_backend import get_search_backend
from app.routers.api.content_responses import (
    build_content_summary_response,
)
from app.utils.pagination import PaginationCursor

logger = get_logger(__name__)


def execute(
    db: Session,
    *,
    user_id: int,
    q: str,
    content_type: str,
    limit: int,
    cursor: str | None,
    offset: int,
) -> ContentListResponse:
    """Return search response for visible content cards."""
    last_id = None
    last_created_at = None
    if cursor:
        try:
            cursor_data = PaginationCursor.decode_cursor(cursor)
            current_filters = {"q": q, "type": content_type}
            if not PaginationCursor.validate_cursor(cursor_data, current_filters):
                raise HTTPException(
                    status_code=400,
                    detail="Cursor invalid: search params changed. Start a new search.",
                )
            last_id = cursor_data["last_id"]
            last_created_at = cursor_data["last_created_at"]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    rows = search_contents(
        db,
        user_id=user_id,
        query_text=q,
        content_type=content_type,
        search_backend=get_search_backend(db),
        cursor=(last_id, last_created_at),
        limit=limit,
        offset=offset,
    )
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    contents = []
    for content, is_read, is_favorited in rows:
        try:
            domain_content = content_to_domain(content)
        except Exception:
            logger.exception(
                "Skipping invalid content row in search_contents",
                extra={
                    "component": "search_content_cards",
                    "operation": "content_to_domain",
                    "item_id": content.id,
                },
            )
            continue
        image_url, thumbnail_url = resolve_image_urls(domain_content)
        if not is_ready_for_list(domain_content, image_url):
            continue
        contents.append(
            build_content_summary_response(
                content=content,
                domain_content=domain_content,
                is_read=bool(is_read),
                is_favorited=bool(is_favorited),
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
            filters={"q": q, "type": content_type},
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
