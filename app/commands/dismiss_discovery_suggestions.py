"""Application commands for dismissing discovery suggestions."""

from __future__ import annotations

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.models.api.discovery import DiscoveryDismissRequest, DiscoveryDismissResponse
from app.models.db import FeedDiscoverySuggestion
from app.repositories.discovery_repository import list_user_suggestions_by_ids


def execute(
    db: Session,
    *,
    user_id: int,
    payload: DiscoveryDismissRequest,
) -> DiscoveryDismissResponse:
    """Dismiss selected discovery suggestions owned by the current user."""
    suggestions = list_user_suggestions_by_ids(
        db,
        user_id=user_id,
        suggestion_ids=payload.suggestion_ids,
    )

    dismissed: list[int] = []
    for suggestion in suggestions:
        suggestion.status = "dismissed"
        if suggestion.id is not None:
            dismissed.append(suggestion.id)

    db.commit()
    return DiscoveryDismissResponse(dismissed=dismissed)


def clear_all(db: Session, *, user_id: int) -> DiscoveryDismissResponse:
    """Dismiss all non-dismissed discovery suggestions owned by the current user."""
    statement = (
        update(FeedDiscoverySuggestion)
        .where(FeedDiscoverySuggestion.user_id == user_id)
        .where(
            or_(
                FeedDiscoverySuggestion.status.is_(None),
                FeedDiscoverySuggestion.status != "dismissed",
            )
        )
        .values(status="dismissed")
        .returning(FeedDiscoverySuggestion.id)
    )
    dismissed = [
        int(suggestion_id)
        for suggestion_id in db.execute(statement).scalars().all()
        if suggestion_id is not None
    ]
    db.commit()
    return DiscoveryDismissResponse(dismissed=dismissed)
