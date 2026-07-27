from __future__ import annotations

from app.services.exa_client import ExaSearchResult
from app.services.onboarding.internal_models import _DiscoverOutput
from app.services.onboarding.search import _run_discovery_exa_queries
from app.services.onboarding.suggestion_projection import _build_discovery_response


def test_build_discovery_response_does_not_backfill_static_defaults() -> None:
    response = _build_discovery_response(
        _DiscoverOutput(substacks=[], podcasts=[], subreddits=[]),
    )
    assert len(response.recommended_substacks) == 0
    assert len(response.recommended_pods) == 0
    assert len(response.recommended_subreddits) == 0


def test_run_discovery_exa_queries_uses_query_metadata(monkeypatch) -> None:
    def fake_exa_search(
        query: str,
        num_results: int = 5,
        max_characters: int = 2000,
        category: str | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        **_kwargs,
    ) -> list[ExaSearchResult]:
        _ = (num_results, max_characters, category, include_domains, exclude_domains)
        return [
            ExaSearchResult(
                title=f"Result {query}",
                url=f"https://{query}.example.com/feed.xml",
                snippet=f"snippet {query}",
            )
        ]

    monkeypatch.setattr("app.services.onboarding.search.exa_search", fake_exa_search)

    queries = ["whales feed", "parks feed", "legaltech feed"]
    results = _run_discovery_exa_queries(
        queries,
        num_results=2,
        lane_name="Nature lane",
        lane_target="feeds",
    )

    assert [item.query for item in results] == queries
    assert all(item.lane_name == "Nature lane" for item in results)
    assert all(item.lane_target == "feeds" for item in results)
