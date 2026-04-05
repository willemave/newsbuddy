"""Application queries for stats endpoints."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.api.common import (
    LongFormStatsResponse,
    ProcessingCountResponse,
    UnreadCountsResponse,
)
from app.repositories import stats_repository


def get_unread_counts(db: Session, *, user_id: int) -> UnreadCountsResponse:
    """Return unread-count stats response."""
    return UnreadCountsResponse(**stats_repository.get_unread_counts(db, user_id=user_id))


def get_processing_count(db: Session, *, user_id: int) -> ProcessingCountResponse:
    """Return processing-count stats response."""
    return ProcessingCountResponse(**stats_repository.get_processing_count(db, user_id=user_id))


def get_long_form_stats(db: Session, *, user_id: int) -> LongFormStatsResponse:
    """Return long-form stats response."""
    return LongFormStatsResponse(**stats_repository.get_long_form_stats(db, user_id=user_id))
