"""Authenticated user feedback endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.commands import submit_feedback
from app.core.db import get_db_session
from app.core.deps import get_current_user, require_user_id
from app.models.api.feedback import SubmitFeedbackRequest, SubmitFeedbackResponse
from app.models.db.users import User

router = APIRouter(tags=["feedback"])


@router.post(
    "/feedback",
    response_model=SubmitFeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_feedback(
    payload: SubmitFeedbackRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> SubmitFeedbackResponse:
    """Store feedback from the authenticated user."""
    return submit_feedback.execute(
        db,
        user_id=require_user_id(current_user),
        payload=payload,
    )
