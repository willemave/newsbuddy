"""Machine-oriented additive API surface for the remote agent CLI."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.application.commands import (
    complete_agent_onboarding,
    generate_agent_digest,
    start_agent_onboarding,
)
from app.application.queries import (
    get_agent_onboarding_status,
    get_job_status,
    search_external_results,
)
from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user
from app.models.user import User
from app.routers.api.models import (
    AgentDigestRequest,
    AgentDigestResponse,
    AgentOnboardingCompleteRequest,
    AgentOnboardingStartRequest,
    AgentOnboardingStartResponse,
    AgentSearchRequest,
    AgentSearchResponse,
    JobStatusResponse,
    OnboardingDiscoveryStatusResponse,
)

router = APIRouter(tags=["agent"])


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(
    job_id: int,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> JobStatusResponse:
    """Return async job status."""
    del current_user
    return get_job_status.execute(db, job_id=job_id)


@router.post("/agent/search", response_model=AgentSearchResponse)
def search_agent(
    payload: AgentSearchRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> AgentSearchResponse:
    """Search external/provider-backed sources for the agent CLI."""
    del current_user
    return search_external_results.execute(
        query=payload.query,
        limit=payload.limit,
        include_podcasts=payload.include_podcasts,
    )


@router.post("/agent/onboarding", response_model=AgentOnboardingStartResponse)
async def start_onboarding(
    payload: AgentOnboardingStartRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AgentOnboardingStartResponse:
    """Start simplified async onboarding."""
    return await start_agent_onboarding.execute(db, user_id=current_user.id, payload=payload)


@router.get(
    "/agent/onboarding/{run_id}",
    response_model=OnboardingDiscoveryStatusResponse,
)
def get_onboarding(
    run_id: int,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> OnboardingDiscoveryStatusResponse:
    """Return onboarding run status."""
    return get_agent_onboarding_status.execute(db, user_id=current_user.id, run_id=run_id)


@router.post(
    "/agent/onboarding/{run_id}/complete",
    response_model=dict,
)
def complete_onboarding(
    run_id: int,
    payload: AgentOnboardingCompleteRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Complete onboarding from simplified selections."""
    response = complete_agent_onboarding.execute(
        db,
        user_id=current_user.id,
        run_id=run_id,
        payload=payload,
    )
    return response.model_dump(mode="json")


@router.post("/agent/digests", response_model=AgentDigestResponse)
def generate_digest(
    payload: AgentDigestRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AgentDigestResponse:
    """Queue arbitrary-window digest generation for agent clients."""
    return generate_agent_digest.execute(db, user_id=current_user.id, payload=payload)
