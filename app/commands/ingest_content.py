"""Shared ingestion command for URL-backed content creation/reuse."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.content_submission import ContentSubmissionResponse, SubmitContentRequest
from app.models.user import User
from app.services.content_submission import submit_user_content


@dataclass(frozen=True)
class IngestContentResult:
    """Stable ingestion result for async URL-backed content submission."""

    content_id: int
    job_id: int | None
    response: ContentSubmissionResponse


def execute(
    db: Session,
    *,
    payload: SubmitContentRequest,
    current_user: User,
    submitted_via: str = "share_sheet",
) -> IngestContentResult:
    """Create or reuse content and enqueue async processing."""
    response = submit_user_content(
        db,
        payload,
        current_user,
        submitted_via=submitted_via,
    )
    return IngestContentResult(
        content_id=response.content_id,
        job_id=response.task_id,
        response=response,
    )
