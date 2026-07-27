"""Exa search execution for onboarding discovery."""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from app.services.exa_client import ExaSearchResult, exa_search
from app.services.onboarding.config import EXA_DISCOVERY_MAX_WORKERS
from app.services.onboarding.internal_models import _DiscoveryWebResult


def _run_exa_queries(
    queries: Iterable[str],
    *,
    num_results: int,
    include_social: bool = False,
    telemetry: dict[str, Any] | None = None,
) -> list[ExaSearchResult]:
    results: list[ExaSearchResult] = []
    exclude_domains: list[str] | None = [] if include_social else None
    for query in queries:
        results.extend(
            exa_search(
                query,
                num_results=num_results,
                max_characters=1200,
                exclude_domains=exclude_domains,
                telemetry=telemetry,
            )
        )
    return results


def _run_discovery_exa_queries(
    queries: Iterable[str],
    *,
    num_results: int,
    include_social: bool = False,
    lane_name: str | None = None,
    lane_target: Literal["feeds", "podcasts", "reddit"] | None = None,
    telemetry: dict[str, Any] | None = None,
) -> list[_DiscoveryWebResult]:
    """Run Exa queries and attach onboarding discovery metadata."""
    results: list[_DiscoveryWebResult] = []
    exclude_domains: list[str] | None = [] if include_social else None
    cleaned_queries = [
        query.strip() for query in queries if isinstance(query, str) and query.strip()
    ]
    if not cleaned_queries:
        return results

    max_workers = min(EXA_DISCOVERY_MAX_WORKERS, len(cleaned_queries))

    def _search_query(query: str) -> tuple[str, list[ExaSearchResult]]:
        return (
            query,
            exa_search(
                query,
                num_results=num_results,
                max_characters=1200,
                exclude_domains=exclude_domains,
                telemetry=telemetry,
            ),
        )

    # Preserve query order while still running network-bound Exa calls concurrently.
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        query_results = list(executor.map(_search_query, cleaned_queries))

    for query, raw_results in query_results:
        for item in raw_results:
            # Preserve each Exa result and include lane/query context for prompt balancing.
            results.append(
                _DiscoveryWebResult(
                    title=item.title,
                    url=item.url,
                    snippet=item.snippet,
                    published_date=item.published_date,
                    query=query,
                    lane_name=lane_name,
                    lane_target=lane_target,
                )
            )
    return results
