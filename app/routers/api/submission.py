"""Endpoint for one-off user submissions."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.commands import submit_content as submit_content_command
from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user, require_user_id
from app.models.api.common import (
    SubmissionStatusListResponse,
)
from app.models.content_submission import ContentSubmissionResponse, SubmitContentRequest
from app.models.user import User
from app.queries import list_submission_statuses as list_submission_statuses_query

router = APIRouter()


@router.post(
    "/submit",
    response_model=ContentSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": ContentSubmissionResponse,
            "description": "Existing content matched and reused",
        }
    },
    summary="Submit a one-off URL for processing",
    description="Submit article or podcast URLs for processing. Only http/https URLs are accepted.",
)
async def submit_content(
    payload: SubmitContentRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ContentSubmissionResponse | JSONResponse:
    """Create or reuse content for a user-submitted URL and enqueue processing."""
    try:
        result = submit_content_command.execute(
            db,
            payload=payload,
            current_user=current_user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    status_code = status.HTTP_200_OK if result.already_exists else status.HTTP_201_CREATED
    return JSONResponse(status_code=status_code, content=result.model_dump(mode="json"))


@router.get(
    "/submissions/list",
    response_model=SubmissionStatusListResponse,
    summary="List user-submitted content still processing or failed",
    description=(
        "Returns self-submitted content items that are not yet completed, including "
        "processing, failed, and skipped statuses."
    ),
)
async def list_submission_statuses(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    cursor: str | None = Query(None, description="Pagination cursor for next page"),
    limit: int = Query(
        25,
        ge=1,
        le=100,
        description="Number of items per page (max 100)",
    ),
) -> SubmissionStatusListResponse:
    """List status information for the current user's submissions."""
    user_id = require_user_id(current_user)
    try:
        return list_submission_statuses_query.execute(
            db,
            user_id=user_id,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
