"""Application command for user content submission."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.commands.ingest_content import execute as ingest_content
from app.models.content_submission import SubmitContentRequest
from app.models.user import User


def execute(
    db: Session,
    *,
    payload: SubmitContentRequest,
    current_user: User,
    submitted_via: str = "share_sheet",
):
    """Submit a URL through the shared ingestion path."""
    return ingest_content(
        db,
        payload=payload,
        current_user=current_user,
        submitted_via=submitted_via,
    ).response
