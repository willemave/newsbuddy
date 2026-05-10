"""Admin onboarding preview tools."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.admin_web.templates import templates
from app.core.deps import require_admin
from app.models.api.onboarding import (
    OnboardingAudioDiscoverRequest,
    OnboardingAudioLanePreviewResponse,
)
from app.services.onboarding import preview_audio_lane_plan

router = APIRouter(tags=["admin"])


@router.get("/onboarding/lane-preview", response_class=HTMLResponse)
def onboarding_lane_preview_page(
    request: Request,
    _: None = Depends(require_admin),
) -> HTMLResponse:
    """Render admin tool for onboarding lane preview."""
    return templates.TemplateResponse(
        request,
        "onboarding_lane_preview.html",
        {
            "request": request,
        },
    )


@router.post(
    "/onboarding/lane-preview",
    response_model=OnboardingAudioLanePreviewResponse,
)
async def onboarding_lane_preview(
    payload: OnboardingAudioDiscoverRequest,
    _: None = Depends(require_admin),
) -> OnboardingAudioLanePreviewResponse:
    """Preview generated onboarding lanes from transcript input."""
    try:
        return await preview_audio_lane_plan(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
