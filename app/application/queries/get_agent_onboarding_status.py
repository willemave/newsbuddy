"""Application query for simplified agent onboarding status."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.onboarding import get_onboarding_discovery_status


def execute(db: Session, *, user_id: int, run_id: int):
    """Return current status for a simplified agent onboarding run."""
    return get_onboarding_discovery_status(db, user_id, run_id)
