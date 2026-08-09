from __future__ import annotations

import threading
from types import SimpleNamespace

from app.models.api.onboarding import OnboardingFastDiscoverRequest, OnboardingProfileRequest
from app.services.exa_client import ExaSearchResult
from app.services.onboarding.config import (
    ENRICH_TIMEOUT_SECONDS,
    FAST_DISCOVER_TIMEOUT_SECONDS,
    PROFILE_TIMEOUT_SECONDS,
)
from app.services.onboarding.discovery_run import fast_discover, run_discover_enrich
from app.services.onboarding.internal_models import _DiscoverOutput
from app.services.onboarding.llm_plans import build_onboarding_profile
from app.services.onboarding.search import _run_discovery_exa_queries, _run_exa_queries
from app.services.onboarding.suggestion_projection import _build_discovery_response


def test_build_discovery_response_does_not_backfill_static_defaults() -> None:
    response = _build_discovery_response(
        _DiscoverOutput(substacks=[], podcasts=[], subreddits=[]),
        detector=SimpleNamespace(),
    )
    assert len(response.recommended_substacks) == 0
    assert len(response.recommended_pods) == 0
    assert len(response.recommended_subreddits) == 0


def test_run_discovery_exa_queries_uses_query_metadata(monkeypatch) -> None:
    captured_timeouts: list[float | None] = []

    def fake_exa_search(
        query: str,
        num_results: int = 5,
        max_characters: int = 2000,
        category: str | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        request_timeout_seconds: float | None = None,
        **_kwargs,
    ) -> list[ExaSearchResult]:
        _ = (num_results, max_characters, category, include_domains, exclude_domains)
        captured_timeouts.append(request_timeout_seconds)
        return [
            ExaSearchResult(
                title=f"Result {query}",
                url=f"https://{query}.example.com/feed.xml",
                snippet=f"snippet {query}",
            )
        ]

    monkeypatch.setattr("app.services.exa_client.exa_search", fake_exa_search)

    queries = ["whales feed", "parks feed", "legaltech feed"]
    results = _run_discovery_exa_queries(
        queries,
        num_results=2,
        lane_name="Nature lane",
        lane_target="feeds",
        request_timeout_seconds=7.5,
    )

    assert [item.query for item in results] == queries
    assert all(item.lane_name == "Nature lane" for item in results)
    assert all(item.lane_target == "feeds" for item in results)
    assert captured_timeouts == [7.5, 7.5, 7.5]


def test_run_exa_queries_overlaps_calls_and_preserves_query_order(monkeypatch) -> None:
    active = 0
    peak_active = 0
    active_lock = threading.Lock()
    overlap_started = threading.Event()

    def fake_exa_search(query: str, **_kwargs) -> list[ExaSearchResult]:
        nonlocal active, peak_active
        with active_lock:
            active += 1
            peak_active = max(peak_active, active)
            if active >= 2:
                overlap_started.set()

        assert overlap_started.wait(timeout=1)
        with active_lock:
            active -= 1
        return [
            ExaSearchResult(
                title=f"Result {query}",
                url=f"https://{query}.example.com/feed.xml",
                snippet=f"snippet {query}",
            )
        ]

    monkeypatch.setattr("app.services.exa_client.exa_search", fake_exa_search)

    results = _run_exa_queries(["first", "second", "third"], num_results=1)

    assert peak_active > 1
    assert [result.title for result in results] == [
        "Result first",
        "Result second",
        "Result third",
    ]


def test_profile_search_propagates_profile_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_exa_queries(*_args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "app.services.onboarding.llm_plans._run_exa_queries",
        fake_run_exa_queries,
    )

    response = build_onboarding_profile(
        OnboardingProfileRequest(first_name="Ada", interest_topics=["AI"])
    )

    assert response.candidate_sources == []
    assert captured["request_timeout_seconds"] == PROFILE_TIMEOUT_SECONDS


def test_fast_discovery_search_propagates_fast_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_discovery_exa_queries(*_args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "app.services.onboarding.discovery_run._run_discovery_exa_queries",
        fake_run_discovery_exa_queries,
    )

    response = fast_discover(
        OnboardingFastDiscoverRequest(
            profile_summary="Interested in AI",
            inferred_topics=["AI"],
        ),
        user_id=7,
    )

    assert response.recommended_substacks == []
    assert captured["request_timeout_seconds"] == FAST_DISCOVER_TIMEOUT_SECONDS


def test_enrichment_search_propagates_enrichment_timeout(
    db_session,
    user_factory,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    user = user_factory()

    def fake_run_discovery_exa_queries(*_args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "app.services.onboarding.discovery_run._run_discovery_exa_queries",
        fake_run_discovery_exa_queries,
    )

    result = run_discover_enrich(
        db_session,
        user.id,
        profile_summary="Interested in AI",
        inferred_topics=["AI"],
    )

    assert result.success is True
    assert captured["request_timeout_seconds"] == ENRICH_TIMEOUT_SECONDS
