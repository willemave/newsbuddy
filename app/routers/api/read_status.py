"""Read status management endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.commands import mark_read as mark_read_command
from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user, require_user_id
from app.core.logging import get_logger
from app.models.api.common import BulkMarkReadRequest, ContentListResponse
from app.models.user import User
from app.queries import get_recently_read as get_recently_read_query

logger = get_logger(__name__)

router = APIRouter()


@router.post(
    "/{content_id}/mark-read",
    summary="Mark content as read",
    description="Mark a specific content item as read.",
    responses={
        200: {"description": "Content marked as read successfully"},
        404: {"description": "Content not found"},
        401: {"description": "Authentication required"},
    },
)
async def mark_content_read(
    content_id: Annotated[int, Path(..., description="Content ID", gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Mark content as read."""
    user_id = require_user_id(current_user)
    logger.info(
        "[API] POST /{content_id}/mark-read called | user_id=%s content_id=%s",
        user_id,
        content_id,
    )
    return mark_read_command.mark_read(db, user_id=user_id, content_id=content_id)


@router.delete(
    "/{content_id}/mark-unread",
    summary="Mark content as unread",
    description="Remove the read status from a specific content item.",
    responses={
        200: {"description": "Content marked as unread successfully"},
        404: {"description": "Content not found"},
        401: {"description": "Authentication required"},
    },
)
async def mark_content_unread(
    content_id: Annotated[int, Path(..., description="Content ID", gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Mark content as unread by removing its read status."""
    user_id = require_user_id(current_user)
    logger.info(
        "[API] DELETE /{content_id}/mark-unread called | user_id=%s content_id=%s",
        user_id,
        content_id,
    )
    return mark_read_command.mark_unread(db, user_id=user_id, content_id=content_id)


@router.post(
    "/bulk-mark-read",
    summary="Bulk mark content as read",
    description="Mark multiple content items as read in a single request.",
    responses={
        200: {"description": "Content items marked as read successfully"},
        400: {"description": "Invalid content IDs provided"},
        401: {"description": "Authentication required"},
    },
)
async def bulk_mark_read(
    request: BulkMarkReadRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Mark multiple content items as read."""
    user_id = require_user_id(current_user)
    logger.info(
        "[API] POST /bulk-mark-read called | user_id=%s content_ids=%s count=%s",
        user_id,
        request.content_ids,
        len(request.content_ids),
    )
    return mark_read_command.bulk_mark_read(
        db,
        user_id=user_id,
        content_ids=request.content_ids,
    )


@router.get(
    "/recently-read/list",
    response_model=ContentListResponse,
    summary="Get recently read content",
    description=(
        "Retrieve all read content items sorted by read time "
        "(most recent first) with cursor-based pagination."
    ),
    responses={
        401: {"description": "Authentication required"},
    },
)
async def get_recently_read(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    cursor: str | None = Query(None, description="Pagination cursor for next page"),
    limit: int = Query(
        25,
        ge=1,
        le=100,
        description="Number of items per page (max 100)",
    ),
) -> ContentListResponse:
    """Get all recently read content with cursor-based pagination, sorted by read time."""
    user_id = require_user_id(current_user)
    logger.info(
        "[API] GET /recently-read/list called | user_id=%s cursor=%s limit=%s",
        user_id,
        cursor[:20] + "..." if cursor else None,
        limit,
    )
    return get_recently_read_query.execute(
        db,
        user_id=user_id,
        cursor=cursor,
        limit=limit,
    )
