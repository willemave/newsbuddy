"""Application command for storing user feedback."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.api.feedback import SubmitFeedbackRequest, SubmitFeedbackResponse
from app.models.db import UserFeedback


def execute(
    db: Session,
    *,
    user_id: int,
    payload: SubmitFeedbackRequest,
) -> SubmitFeedbackResponse:
    """Persist authenticated user feedback."""
    feedback = UserFeedback(
        user_id=user_id,
        message=payload.message,
        source=payload.source,
        app_version=payload.app_version,
        build_number=payload.build_number,
        platform=payload.platform,
        os_version=payload.os_version,
        device_model=payload.device_model,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)

    if feedback.id is None:
        raise ValueError("Feedback insert did not produce an id")
    return SubmitFeedbackResponse(status="success", feedback_id=int(feedback.id))
