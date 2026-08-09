"""Exa search execution for onboarding discovery."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from app.services.exa_client import ExaSearchResult, exa_search_many
from app.services.onboarding.config import EXA_DISCOVERY_MAX_WORKERS
from app.services.onboarding.internal_models import _DiscoveryWebResult


def _run_threaded_exa_queries(
    queries: list[str],
    *,
    num_results: int,
    include_social: bool,
    telemetry: dict[str, Any] | None,
    request_timeout_seconds: float | None,
) -> list[tuple[str, list[ExaSearchResult]]]:
    return exa_search_many(
        queries,
        max_workers=EXA_DISCOVERY_MAX_WORKERS,
        num_results=num_results,
        max_characters=1200,
        exclude_domains=[] if include_social else None,
        telemetry=telemetry,
        request_timeout_seconds=request_timeout_seconds,
    )


def _run_exa_queries(
    queries: Iterable[str],
    *,
    num_results: int,
    include_social: bool = False,
    telemetry: dict[str, Any] | None = None,
    request_timeout_seconds: float | None = None,
) -> list[ExaSearchResult]:
    query_results = _run_threaded_exa_queries(
        list(queries),
        num_results=num_results,
        include_social=include_social,
        telemetry=telemetry,
        request_timeout_seconds=request_timeout_seconds,
    )
    return [result for _query, items in query_results for result in items]


def _run_discovery_exa_queries(
    queries: Iterable[str],
    *,
    num_results: int,
    include_social: bool = False,
    lane_name: str | None = None,
    lane_target: Literal["feeds", "podcasts", "reddit"] | None = None,
    telemetry: dict[str, Any] | None = None,
    request_timeout_seconds: float | None = None,
) -> list[_DiscoveryWebResult]:
    """Run Exa queries and attach onboarding discovery metadata."""
    clean_queries = [query.strip() for query in queries if isinstance(query, str) and query.strip()]
    query_results = _run_threaded_exa_queries(
        clean_queries,
        num_results=num_results,
        include_social=include_social,
        telemetry=telemetry,
        request_timeout_seconds=request_timeout_seconds,
    )
    return [
        _DiscoveryWebResult(
            title=item.title,
            url=item.url,
            snippet=item.snippet,
            published_date=item.published_date,
            query=query,
            lane_name=lane_name,
            lane_target=lane_target,
        )
        for query, items in query_results
        for item in items
    ]
