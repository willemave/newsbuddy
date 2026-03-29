from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.routers.api.models import OnboardingFastDiscoverRequest, OnboardingFastDiscoverResponse
from app.services.onboarding import (
    AUDIO_PLAN_FALLBACK_MODELS,
    AUDIO_PLAN_MODEL,
    DISCOVERY_FALLBACK_MODELS,
    FAST_DISCOVER_MODEL,
    _AudioLane,
    _AudioPlanOutput,
    _build_audio_lane_plan_with_metadata,
    _DiscoverOutput,
    _DiscoverSuggestion,
    _run_audio_plan_with_fallback,
    _run_discover_output_with_fallback,
    fast_discover,
)


def test_default_onboarding_fallback_order() -> None:
    assert DISCOVERY_FALLBACK_MODELS == ()
    assert AUDIO_PLAN_FALLBACK_MODELS == ()


def test_discover_generation_does_not_use_secondary_model(monkeypatch):
    attempts: list[str] = []

    class FailingAgent:
        def run_sync(self, _prompt, model_settings=None):  # noqa: ANN001
            raise TimeoutError("primary timeout")

    class SuccessAgent:
        def run_sync(self, _prompt, model_settings=None):  # noqa: ANN001
            return SimpleNamespace(
                data=_DiscoverOutput(
                    substacks=[
                        _DiscoverSuggestion(
                            title="Example",
                            feed_url="https://example.com/feed",
                        )
                    ]
                )
            )

    def fake_get_basic_agent(model_spec, _output_cls, _system_prompt):
        attempts.append(model_spec)
        if model_spec == FAST_DISCOVER_MODEL:
            return FailingAgent()
        return SuccessAgent()

    monkeypatch.setattr(
        "app.services.onboarding.DISCOVERY_FALLBACK_MODELS",
        ("openai:gpt-5.4-mini",),
    )
    monkeypatch.setattr("app.services.onboarding.get_basic_agent", fake_get_basic_agent)

    with pytest.raises(TimeoutError, match="primary timeout"):
        _run_discover_output_with_fallback(
            prompt="test prompt",
            timeout_seconds=12,
            operation="test_discover_fallback",
        )

    assert attempts == [FAST_DISCOVER_MODEL]


def test_fast_discover_returns_defaults_when_generation_fails(monkeypatch) -> None:
    fallback = OnboardingFastDiscoverResponse()

    monkeypatch.setattr("app.services.onboarding._load_curated_defaults", lambda: {})
    monkeypatch.setattr(
        "app.services.onboarding._run_discovery_exa_queries",
        lambda *_args, **_kwargs: ["result"],
    )
    monkeypatch.setattr(
        "app.services.onboarding._select_prompt_results",
        lambda results, lane_balanced=False: results,
    )
    monkeypatch.setattr(
        "app.services.onboarding._format_discovery_prompt",
        lambda *_args, **_kwargs: "prompt",
    )
    monkeypatch.setattr(
        "app.services.onboarding._run_discover_output_with_fallback",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("primary timeout")),
    )
    monkeypatch.setattr(
        "app.services.onboarding._fast_discover_from_defaults",
        lambda *_args, **_kwargs: fallback,
    )

    response = fast_discover(
        OnboardingFastDiscoverRequest(
            profile_summary="AI engineer",
            inferred_topics=["AI"],
        )
    )

    assert response == fallback


@pytest.mark.asyncio
async def test_audio_plan_generation_does_not_use_secondary_model(monkeypatch):
    attempts: list[str] = []

    class FailingAgent:
        async def run(self, _prompt, model_settings=None):  # noqa: ANN001
            raise TimeoutError("primary timeout")

    class SuccessAgent:
        async def run(self, _prompt, model_settings=None):  # noqa: ANN001
            return SimpleNamespace(
                data=_AudioPlanOutput(
                    topic_summary="AI topics",
                    inferred_topics=["AI"],
                    lanes=[
                        _AudioLane(
                            name="Lane",
                            goal="Goal",
                            target="feeds",
                            queries=["ai newsletter updates", "ai rss feeds"],
                        )
                    ],
                )
            )

    def fake_get_basic_agent(model_spec, _output_cls, _system_prompt):
        attempts.append(model_spec)
        if model_spec == AUDIO_PLAN_MODEL:
            return FailingAgent()
        return SuccessAgent()

    monkeypatch.setattr(
        "app.services.onboarding.AUDIO_PLAN_FALLBACK_MODELS",
        ("openai:gpt-5.4-mini",),
    )
    monkeypatch.setattr("app.services.onboarding.get_basic_agent", fake_get_basic_agent)

    with pytest.raises(TimeoutError, match="primary timeout"):
        await _run_audio_plan_with_fallback(
            prompt="test prompt",
            timeout_seconds=8,
        )

    assert attempts == [AUDIO_PLAN_MODEL]


@pytest.mark.asyncio
async def test_audio_plan_build_returns_fallback_on_generation_error(monkeypatch) -> None:
    async def fail_generation(*_args, **_kwargs):
        raise TimeoutError("primary timeout")

    monkeypatch.setattr(
        "app.services.onboarding._run_audio_plan_with_fallback",
        fail_generation,
    )

    plan, used_fallback, fallback_reason = await _build_audio_lane_plan_with_metadata(
        "AI chip infrastructure transcript",
        "en-US",
    )

    assert used_fallback is True
    assert fallback_reason == "primary timeout"
    assert plan.topic_summary
    assert any(lane.target == "reddit" for lane in plan.lanes)
