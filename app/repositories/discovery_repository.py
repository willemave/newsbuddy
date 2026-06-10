"""Repository helpers for discovery suggestions."""

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.models.db import FeedDiscoverySuggestion


def list_user_suggestions_by_ids(
    db: Session,
    *,
    user_id: int,
    suggestion_ids: Sequence[int],
) -> list[FeedDiscoverySuggestion]:
    """Return discovery suggestions owned by a user for the given ids."""
    if not suggestion_ids:
        return []
    return (
        db.query(FeedDiscoverySuggestion)
        .filter(
            FeedDiscoverySuggestion.user_id == user_id,
            FeedDiscoverySuggestion.id.in_(suggestion_ids),
        )
        .all()
    )
