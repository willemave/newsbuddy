from __future__ import annotations

from typing import Any, cast

from app.models.api.common import OnboardingFastDiscoverRequest, OnboardingSuggestion
from app.services.exa_client import ExaSearchResult
from app.services.onboarding import (
    _build_discovery_response,
    _curated_fill_in_candidates,
    _DiscoverOutput,
    _DiscoverSuggestion,
    _fast_discover_from_defaults,
    _format_discovery_prompt,
)


def _curated_defaults() -> dict[str, list[OnboardingSuggestion]]:
    return {
        "substack": [
            OnboardingSuggestion(
                suggestion_type="substack",
                title="Example Substack",
                feed_url="https://example.substack.com/feed",
                site_url="https://example.substack.com",
                is_default=True,
            )
        ],
        "atom": [],
        "reddit": [
            OnboardingSuggestion(
                suggestion_type="reddit",
                title="MachineLearning",
                subreddit="MachineLearning",
                site_url="https://www.reddit.com/r/MachineLearning/",
                is_default=True,
            )
        ],
    }


def test_format_discovery_prompt_includes_curated_fill_ins() -> None:
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
    curated = _curated_defaults()

    prompt = _format_discovery_prompt(
        request,
        cast(list[Any], results),
        _curated_fill_in_candidates(curated),
    )

    assert "web_results:" in prompt
    assert "curated_fill_ins:" in prompt
    assert "feeds:" in prompt
    assert "podcasts:" not in prompt
    assert "reddit:" in prompt
    assert "subreddit: MachineLearning" in prompt


def test_fast_discover_defaults_backfills_rationale() -> None:
    response = _fast_discover_from_defaults(
        _curated_defaults(),
        profile_summary="Biology, psychology, and business books",
        inferred_topics=["biology", "psychology"],
    )

    for item in (
        response.recommended_substacks + response.recommended_pods + response.recommended_subreddits
    ):
        assert item.rationale
        assert item.rationale.strip()


def test_build_discovery_response_backfills_merged_rationale(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.onboarding.resolve_feed_candidate",
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
        _curated_defaults(),
        profile_summary="AI and startup strategy",
        inferred_topics=["AI", "startups"],
    )

    assert response.recommended_substacks[0].feed_url == "https://fresh.example.com/feed.xml"
    assert response.recommended_substacks[0].rationale == "Freshly discovered AI source."

    for item in (
        response.recommended_substacks + response.recommended_pods + response.recommended_subreddits
    ):
        assert item.rationale
        assert item.rationale.strip()
