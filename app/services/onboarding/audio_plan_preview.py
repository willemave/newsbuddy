"""API presentation helpers for onboarding audio plans."""

from app.models.api.onboarding import OnboardingAudioLanePreview
from app.services.onboarding.config import FAST_DISCOVER_EXA_RESULTS
from app.services.onboarding.internal_models import _AudioLane


def serialize_audio_lane_preview(lane: _AudioLane) -> OnboardingAudioLanePreview:
    """Map one internal audio lane to its preview response."""
    return OnboardingAudioLanePreview(
        name=lane.name,
        goal=lane.goal,
        target=lane.target,
        queries=list(lane.queries),
        include_social=lane.target == "reddit",
        exa_results_per_query=FAST_DISCOVER_EXA_RESULTS,
    )
