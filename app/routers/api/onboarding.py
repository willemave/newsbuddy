"""Onboarding endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.db import get_db_session
from app.core.deps import get_current_user, require_user_id
from app.models.api.common import (
    OnboardingAudioDiscoverRequest,
    OnboardingAudioDiscoverResponse,
    OnboardingCompleteRequest,
    OnboardingCompleteResponse,
    OnboardingDiscoveryStatusResponse,
    OnboardingFastDiscoverRequest,
    OnboardingFastDiscoverResponse,
    OnboardingProfileRequest,
    OnboardingProfileResponse,
    OnboardingTutorialResponse,
    OnboardingVoiceParseRequest,
    OnboardingVoiceParseResponse,
)
from app.models.user import User
from app.services.onboarding import (
    build_onboarding_profile,
    complete_onboarding,
    fast_discover,
    get_onboarding_discovery_status,
    mark_tutorial_complete,
    parse_onboarding_voice,
    start_audio_discovery,
)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.post(
    "/profile",
    response_model=OnboardingProfileResponse,
    summary="Build onboarding profile",
)
async def build_profile(
    payload: OnboardingProfileRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> OnboardingProfileResponse:
    """Build onboarding profile summary."""
    _ = current_user
    return await run_in_threadpool(build_onboarding_profile, payload)


@router.post(
    "/parse-voice",
    response_model=OnboardingVoiceParseResponse,
    summary="Parse onboarding voice transcript",
)
async def parse_voice(
    payload: OnboardingVoiceParseRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> OnboardingVoiceParseResponse:
    """Parse onboarding transcript into profile fields."""
    _ = current_user
    return await run_in_threadpool(parse_onboarding_voice, payload)


@router.post(
    "/fast-discover",
    response_model=OnboardingFastDiscoverResponse,
    summary="Fast onboarding discovery",
)
async def run_fast_discover(
    payload: OnboardingFastDiscoverRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> OnboardingFastDiscoverResponse:
    """Return fast discovery suggestions for onboarding."""
    _ = current_user
    return await run_in_threadpool(fast_discover, payload)


@router.post(
    "/audio-discover",
    response_model=OnboardingAudioDiscoverResponse,
    summary="Start onboarding audio discovery",
)
async def start_audio_discovery_flow(
    payload: OnboardingAudioDiscoverRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> OnboardingAudioDiscoverResponse:
    """Start onboarding discovery from an audio transcript."""
    try:
        return await start_audio_discovery(db, require_user_id(current_user), payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/discovery-status",
    response_model=OnboardingDiscoveryStatusResponse,
    summary="Get onboarding audio discovery status",
)
async def onboarding_discovery_status(
    run_id: int,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> OnboardingDiscoveryStatusResponse:
    """Poll onboarding discovery status for a run."""
    try:
        return get_onboarding_discovery_status(db, require_user_id(current_user), run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/complete",
    response_model=OnboardingCompleteResponse,
    summary="Complete onboarding",
)
async def complete_onboarding_flow(
    payload: OnboardingCompleteRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> OnboardingCompleteResponse:
    """Persist onboarding selections and queue crawlers."""
    try:
        return complete_onboarding(db, require_user_id(current_user), payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/tutorial-complete",
    response_model=OnboardingTutorialResponse,
    summary="Mark onboarding tutorial complete",
)
async def tutorial_complete(
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> OnboardingTutorialResponse:
    """Mark tutorial completion flag for current user."""
    if not mark_tutorial_complete(db, require_user_id(current_user)):
        raise HTTPException(status_code=404, detail="User not found")
    return OnboardingTutorialResponse(has_completed_new_user_tutorial=True)
