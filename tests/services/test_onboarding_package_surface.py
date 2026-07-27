"""Contract tests for the intentionally small onboarding package facade."""

from importlib.util import find_spec

from app.services import onboarding
from app.services.onboarding import (
    audio_plan_heuristics,
    discovery_run,
    entrypoints,
    llm_plans,
    persistence,
    query_heuristics,
    search,
    suggestion_projection,
)


def test_onboarding_package_exports_only_supported_entrypoints_and_constants() -> None:
    assert set(onboarding.__all__) == {
        "AUDIO_PLAN_FALLBACK_MODELS",
        "AUDIO_PLAN_MODEL",
        "AUDIO_PLAN_SYSTEM_PROMPT",
        "AUDIO_PLAN_TIMEOUT_SECONDS",
        "DEFAULT_SOURCE_LIMITS",
        "DISCOVERY_FALLBACK_MODELS",
        "DISCOVERY_PROMPT_MAX_WEB_RESULTS",
        "DISCOVERY_PROMPT_SNIPPET_CHARS",
        "ENRICH_EXA_RESULTS",
        "ENRICH_MAX_QUERIES",
        "ENRICH_TIMEOUT_SECONDS",
        "EXA_DISCOVERY_MAX_WORKERS",
        "FAST_DISCOVER_EXA_RESULTS",
        "FAST_DISCOVER_MAX_QUERIES",
        "FAST_DISCOVER_MODEL",
        "FAST_DISCOVER_SYSTEM_PROMPT",
        "FAST_DISCOVER_TIMEOUT_SECONDS",
        "FEED_CONTENT_SEED_LIMIT",
        "FEED_SUGGESTION_TYPES",
        "NEWS_SEED_LIMIT",
        "ONBOARDING_FEED_DETECTOR",
        "ONBOARDING_FEED_SUGGESTION_LIMIT",
        "ONBOARDING_PRIMARY_MODEL",
        "PROFILE_EXA_RESULTS",
        "PROFILE_MODEL",
        "PROFILE_SYSTEM_PROMPT",
        "PROFILE_TIMEOUT_SECONDS",
        "SCRAPER_SOURCE_BY_TYPE",
        "VOICE_PARSE_MODEL",
        "VOICE_PARSE_SYSTEM_PROMPT",
        "VOICE_PARSE_TIMEOUT_SECONDS",
        "build_onboarding_profile",
        "complete_onboarding",
        "fast_discover",
        "get_onboarding_discovery_status",
        "mark_tutorial_complete",
        "parse_onboarding_voice",
        "preview_audio_lane_plan",
        "run_audio_discovery",
        "run_discover_enrich",
        "start_audio_discovery",
    }
    assert not hasattr(onboarding, "_AudioLane")
    assert not hasattr(onboarding, "_persist_discovery_run")
    assert not hasattr(onboarding, "get_basic_agent")


def test_onboarding_package_reexports_owned_entrypoints() -> None:
    assert onboarding.complete_onboarding is entrypoints.complete_onboarding
    assert onboarding.fast_discover is entrypoints.fast_discover
    assert onboarding.run_audio_discovery is discovery_run.run_audio_discovery
    assert onboarding.run_discover_enrich is discovery_run.run_discover_enrich


def test_onboarding_modules_physically_own_their_implementations() -> None:
    assert find_spec("app.services.onboarding._core") is None
    assert entrypoints.complete_onboarding.__module__ == entrypoints.__name__
    assert discovery_run.run_audio_discovery.__module__ == discovery_run.__name__
    assert llm_plans.build_onboarding_profile.__module__ == llm_plans.__name__
    assert (
        audio_plan_heuristics._fallback_audio_lane_plan.__module__ == audio_plan_heuristics.__name__
    )
    assert persistence._persist_discovery_run.__module__ == persistence.__name__
    assert query_heuristics._build_discovery_queries.__module__ == query_heuristics.__name__
    assert search._run_discovery_exa_queries.__module__ == search.__name__
    assert (
        suggestion_projection._build_discovery_response.__module__ == suggestion_projection.__name__
    )
    assert persistence.__all__ == []
    assert all(not name.startswith("_") for name in llm_plans.__all__)
