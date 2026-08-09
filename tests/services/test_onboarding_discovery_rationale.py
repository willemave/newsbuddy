from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from app.models.api.onboarding import OnboardingFastDiscoverRequest
from app.services.exa_client import ExaSearchResult
from app.services.onboarding.internal_models import (
    _DiscoverOutput,
    _DiscoverSuggestion,
)
from app.services.onboarding.llm_plans import _format_discovery_prompt
from app.services.onboarding.suggestion_projection import _build_discovery_response


def test_format_discovery_prompt_uses_only_web_results() -> None:
    request = OnboardingFastDiscoverRequest(
        profile_summary="AI engineering and product leadership",
        inferred_topics=["AI", "product"],
    )
    results = [
        ExaSearchResult(
            title="AI newsletter list",
            url="https://example.com/ai-newsletters",
            snippet="Top AI newsletters and resources.",
        )
    ]
    prompt = _format_discovery_prompt(
        request,
        cast(list[Any], results),
    )

    assert "web_results:" in prompt
    assert "AI newsletter list" in prompt
    assert "curated_fill_ins:" not in prompt
    assert "subreddit: MachineLearning" not in prompt


def test_build_discovery_response_preserves_generated_rationale(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.onboarding.suggestion_projection.resolve_feed_candidate",
        lambda **kwargs: {"feed_url": kwargs["candidate_feed_urls"][0]},
    )
    output = _DiscoverOutput(
        substacks=[
            _DiscoverSuggestion(
                title="Fresh AI Feed",
                feed_url="https://fresh.example.com/feed.xml",
                site_url="https://fresh.example.com",
                rationale="Freshly discovered AI source.",
            )
        ],
        podcasts=[],
        subreddits=[],
    )
    response = _build_discovery_response(
        output,
        profile_summary="AI and startup strategy",
        inferred_topics=["AI", "startups"],
        detector=SimpleNamespace(),
    )

    assert response.recommended_substacks[0].feed_url == "https://fresh.example.com/feed.xml"
    assert response.recommended_substacks[0].rationale == "Freshly discovered AI source."

    for item in (
        response.recommended_substacks + response.recommended_pods + response.recommended_subreddits
    ):
        assert item.rationale
        assert item.rationale.strip()
