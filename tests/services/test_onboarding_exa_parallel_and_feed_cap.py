from __future__ import annotations

from app.models.api.common import OnboardingSuggestion
from app.services.exa_client import ExaSearchResult
from app.services.onboarding import (
    _build_discovery_response,
    _DiscoverOutput,
    _fast_discover_from_defaults,
    _run_discovery_exa_queries,
)


def _many_feed_defaults() -> dict[str, list[OnboardingSuggestion]]:
    return {
        "substack": [
            OnboardingSuggestion(
                suggestion_type="substack",
                title=f"Substack {idx}",
                feed_url=f"https://substack-{idx}.example.com/feed",
                site_url=f"https://substack-{idx}.example.com",
                is_default=True,
            )
            for idx in range(8)
        ],
        "atom": [
            OnboardingSuggestion(
                suggestion_type="atom",
                title=f"Atom {idx}",
                feed_url=f"https://atom-{idx}.example.com/rss.xml",
                site_url=f"https://atom-{idx}.example.com",
                is_default=True,
            )
            for idx in range(6)
        ],
        "podcast_rss": [
            OnboardingSuggestion(
                suggestion_type="podcast_rss",
                title=f"Podcast {idx}",
                feed_url=f"https://podcast-{idx}.example.com/feed.xml",
                site_url=f"https://podcast-{idx}.example.com",
                is_default=True,
            )
            for idx in range(8)
        ],
        "reddit": [
            OnboardingSuggestion(
                suggestion_type="reddit",
                title=f"Subreddit {idx}",
                site_url=f"https://www.reddit.com/r/subreddit_{idx}/",
                subreddit=f"subreddit_{idx}",
                is_default=True,
            )
            for idx in range(8)
        ],
    }


def test_fast_discover_defaults_caps_feed_suggestions_to_five() -> None:
    response = _fast_discover_from_defaults(_many_feed_defaults())
    assert len(response.recommended_substacks) == 5
    assert len(response.recommended_pods) == 5
    assert len(response.recommended_subreddits) == 5


def test_build_discovery_response_caps_feed_suggestions_to_five() -> None:
    response = _build_discovery_response(
        _DiscoverOutput(substacks=[], podcasts=[], subreddits=[]),
        _many_feed_defaults(),
    )
    assert len(response.recommended_substacks) == 5
    assert len(response.recommended_pods) == 5
    assert len(response.recommended_subreddits) == 5


def test_run_discovery_exa_queries_uses_query_metadata(monkeypatch) -> None:
    def fake_exa_search(
        query: str,
        num_results: int = 5,
        max_characters: int = 2000,
        category: str | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> list[ExaSearchResult]:
        _ = (num_results, max_characters, category, include_domains, exclude_domains)
        return [
            ExaSearchResult(
                title=f"Result {query}",
                url=f"https://{query}.example.com/feed.xml",
                snippet=f"snippet {query}",
            )
        ]

    monkeypatch.setattr("app.services.onboarding.exa_search", fake_exa_search)

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
