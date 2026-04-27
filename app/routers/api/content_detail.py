"""Content detail and chat URL endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.commands import refresh_content_discussion as refresh_content_discussion_command
from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user, require_user_id
from app.core.timing import timed
from app.models.api.common import (
    ChatGPTUrlResponse,
    ContentBodyResponse,
    ContentDetailResponse,
    ContentDiscussionResponse,
)
from app.models.user import User
from app.queries import get_content_body as get_content_body_query
from app.queries import get_content_chat_url as get_content_chat_url_query
from app.queries import get_content_detail as get_content_detail_query
from app.queries import get_content_discussion as get_content_discussion_query

router = APIRouter()


@router.get(
    "/{content_id}",
    response_model=ContentDetailResponse,
    summary="Get content details",
    description="Retrieve detailed information about a specific content item.",
    responses={
        404: {
            "description": "Content not found",
            "content": {"application/json": {"example": {"detail": "Content not found"}}},
        }
    },
)
def get_content_detail(
    content_id: Annotated[int, Path(..., description="Content ID", gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ContentDetailResponse:
    """Get detailed view of a specific content item."""
    user_id = require_user_id(current_user)
    with timed("query content_detail"):
        return get_content_detail_query.execute(
            db,
            user_id=user_id,
            content_id=content_id,
        )


@router.get(
    "/{content_id}/body",
    response_model=ContentBodyResponse,
    summary="Get canonical content body",
    description="Retrieve the canonical body text for a content item via the backend proxy.",
)
def get_content_body(
    content_id: Annotated[int, Path(..., description="Content ID", gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    variant: Annotated[
        str,
        Query(description="Body variant", pattern="^(source|rendered)$"),
    ] = "source",
) -> ContentBodyResponse:
    """Return canonical body text for a content item."""
    return get_content_body_query.execute(
        db,
        user_id=require_user_id(current_user),
        content_id=content_id,
        variant=variant,
    )


@router.get(
    "/{content_id}/discussion",
    response_model=ContentDiscussionResponse,
    summary="Get discussion payload for a content item",
    description=(
        "Return in-app discussion data for the content item. Techmeme items return grouped "
        "discussion links. Hacker News and Reddit items return normalized comments + links."
    ),
    responses={
        404: {
            "description": "Content not found",
            "content": {"application/json": {"example": {"detail": "Content not found"}}},
        }
    },
)
def get_content_discussion(
    content_id: Annotated[int, Path(..., description="Content ID", gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ContentDiscussionResponse:
    """Return stored discussion payload for a content item."""
    return get_content_discussion_query.execute(
        db,
        user_id=require_user_id(current_user),
        content_id=content_id,
    )


@router.post(
    "/{content_id}/discussion/refresh",
    response_model=ContentDiscussionResponse,
    summary="Refresh discussion payload for a content item",
    description=(
        "Fetch the latest in-app discussion data for the content item, persist it, "
        "and return the refreshed payload."
    ),
    responses={
        404: {
            "description": "Content not found",
            "content": {"application/json": {"example": {"detail": "Content not found"}}},
        }
    },
)
def refresh_content_discussion(
    content_id: Annotated[int, Path(..., description="Content ID", gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ContentDiscussionResponse:
    """Refresh and return discussion payload for a content item."""
    return refresh_content_discussion_command.refresh_content_discussion(
        db,
        user_id=require_user_id(current_user),
        content_id=content_id,
    )


@router.get(
    "/{content_id}/chat-url",
    response_model=ChatGPTUrlResponse,
    summary="Get ChatGPT URL for content",
    description="Generate a URL to open ChatGPT with the content's full text for discussion.",
    responses={
        404: {"description": "Content not found"},
    },
)
def get_chatgpt_url(
    content_id: Annotated[int, Path(..., description="Content ID", gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    user_prompt: Annotated[
        str | None,
        Query(max_length=2000, description="Optional user prompt to prepend to chat"),
    ] = None,
) -> ChatGPTUrlResponse:
    """Generate ChatGPT URL for chatting about the content.

    If ``user_prompt`` is provided, it is prepended to the generated prompt so the
    selection the user made in the UI appears as the first message in ChatGPT.
    """
    return get_content_chat_url_query.execute(
        db,
        user_id=require_user_id(current_user),
        content_id=content_id,
        user_prompt=user_prompt,
    )
