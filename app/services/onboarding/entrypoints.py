"""Router-facing onboarding service entrypoints."""

from app.services.onboarding import (
    build_onboarding_profile,
    complete_onboarding,
    fast_discover,
    get_onboarding_discovery_status,
    mark_tutorial_complete,
    parse_onboarding_voice,
    preview_audio_lane_plan,
    start_audio_discovery,
)

__all__ = [
    "build_onboarding_profile",
    "complete_onboarding",
    "fast_discover",
    "get_onboarding_discovery_status",
    "mark_tutorial_complete",
    "parse_onboarding_voice",
    "preview_audio_lane_plan",
    "start_audio_discovery",
]
