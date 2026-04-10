"""Knowledge-save management endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.commands import remove_from_knowledge as remove_from_knowledge_command
from app.commands import save_to_knowledge as save_to_knowledge_command
from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user
from app.models.api.common import ContentListResponse
from app.models.user import User
from app.queries import get_knowledge_library as get_knowledge_library_query

router = APIRouter()


@router.post(
    "/{content_id}/knowledge",
    summary="Save content to knowledge",
    description="Save a specific content item to the user's knowledge library.",
    responses={
        200: {"description": "Content saved to knowledge successfully"},
        404: {"description": "Content not found"},
        401: {"description": "Authentication required"},
    },
)
async def save_to_knowledge(
    content_id: Annotated[int, Path(..., description="Content ID", gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Save content to the authenticated user's knowledge library."""
    return save_to_knowledge_command.execute(db, user_id=current_user.id, content_id=content_id)


@router.delete(
    "/{content_id}/knowledge",
    summary="Remove content from knowledge",
    description="Remove a specific content item from the user's knowledge library.",
    responses={
        200: {"description": "Content removed from knowledge successfully"},
        404: {"description": "Content not found"},
        401: {"description": "Authentication required"},
    },
)
async def remove_from_knowledge(
    content_id: Annotated[int, Path(..., description="Content ID", gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Remove content from the authenticated user's knowledge library."""
    return remove_from_knowledge_command.execute(
        db,
        user_id=current_user.id,
        content_id=content_id,
    )


@router.get(
    "/knowledge/list",
    response_model=ContentListResponse,
    summary="Get saved knowledge library",
    description="Retrieve content saved to the user's knowledge library with pagination.",
    responses={401: {"description": "Authentication required"}},
)
async def get_knowledge_library(
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
    """Get all knowledge-saved content with cursor-based pagination."""
    return get_knowledge_library_query.execute(
        db,
        user_id=current_user.id,
        cursor=cursor,
        limit=limit,
    )
