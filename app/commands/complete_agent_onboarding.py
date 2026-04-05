"""Application command for simplified agent onboarding completion."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.common import (
    AgentOnboardingCompleteRequest,
    OnboardingCompleteRequest,
    OnboardingSelectedSource,
)
from app.models.schema import OnboardingDiscoverySuggestion
from app.services.onboarding import complete_onboarding


def execute(
    db: Session,
    *,
    user_id: int,
    run_id: int,
    payload: AgentOnboardingCompleteRequest,
):
    """Complete onboarding from a simplified accept-all or explicit source selection."""
    suggestions = (
        db.query(OnboardingDiscoverySuggestion)
        .filter(OnboardingDiscoverySuggestion.run_id == run_id)
        .filter(OnboardingDiscoverySuggestion.user_id == user_id)
        .order_by(OnboardingDiscoverySuggestion.id.asc())
        .all()
    )
    if not suggestions:
        raise HTTPException(status_code=404, detail="Onboarding run not found")

    selected_ids = set(payload.source_ids)
    selected_sources: list[OnboardingSelectedSource] = []
    selected_subreddits = list(payload.selected_subreddits)
    for suggestion in suggestions:
        if not payload.accept_all and selected_ids and suggestion.id not in selected_ids:
            continue
        if suggestion.suggestion_type == "reddit":
            if suggestion.subreddit:
                selected_subreddits.append(suggestion.subreddit)
            continue
        if not suggestion.feed_url:
            continue
        selected_sources.append(
            OnboardingSelectedSource(
                suggestion_type=suggestion.suggestion_type,
                title=suggestion.title,
                feed_url=suggestion.feed_url,
                config={},
            )
        )
    return complete_onboarding(
        db,
        user_id,
        OnboardingCompleteRequest(
            selected_sources=selected_sources,
            selected_subreddits=selected_subreddits,
        ),
    )
