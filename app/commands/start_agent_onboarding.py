"""Application command for simplified agent onboarding start."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.api.agent import AgentOnboardingStartRequest, AgentOnboardingStartResponse
from app.models.api.onboarding import OnboardingAudioDiscoverRequest
from app.services.onboarding import start_audio_discovery


async def execute(
    db: Session,
    *,
    user_id: int,
    payload: AgentOnboardingStartRequest,
) -> AgentOnboardingStartResponse:
    """Start simplified async onboarding by reusing the audio-discovery flow."""
    response = await start_audio_discovery(
        db,
        user_id,
        OnboardingAudioDiscoverRequest(transcript=payload.brief, locale=None),
    )
    return AgentOnboardingStartResponse(
        run_id=response.run_id,
        status=response.run_status,
        job_id=None,
    )
