"""User-scoped content statistics endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_readonly_db_session
from app.core.deps import get_current_user
from app.models.user import User
from app.queries import get_stats
from app.models.api.common import (
    LongFormStatsResponse,
    ProcessingCountResponse,
    UnreadCountsResponse,
)

router = APIRouter(prefix="/stats")


@router.get(
    "/unread-counts",
    response_model=UnreadCountsResponse,
    summary="Get unread content counts by type",
    description="Get the total count of unread items for each content type.",
)
def get_unread_counts(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> UnreadCountsResponse:
    """Get unread counts for each content type.

    Optimized to use NOT EXISTS instead of NOT IN for much better performance
    with large read lists (30x faster: ~20ms vs ~650ms).
    """
    return get_stats.get_unread_counts(db, user_id=current_user.id)


@router.get(
    "/processing-count",
    response_model=ProcessingCountResponse,
    summary="Get processing counts",
    description=(
        "Return queued/pending/processing counts for the authenticated user, including "
        "long-form and short-form (news) inbox content."
    ),
)
def get_processing_count(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ProcessingCountResponse:
    """Return processing counts for long-form, news, and total."""
    return get_stats.get_processing_count(db, user_id=current_user.id)


@router.get(
    "/long-form",
    response_model=LongFormStatsResponse,
    summary="Get long-form content stats",
    description=(
        "Return long-form stats for the authenticated user, including totals, read/unread, "
        "knowledge saves, and processing counts."
    ),
)
def get_long_form_stats(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LongFormStatsResponse:
    """Return long-form content stats for the authenticated user."""
    return get_stats.get_long_form_stats(db, user_id=current_user.id)
