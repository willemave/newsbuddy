"""Application query for machine-oriented external search."""

from __future__ import annotations

from app.models.api.common import AgentSearchResponse, AgentSearchResultResponse
from app.services.exa_client import exa_search
from app.services.podcast_search import search_podcast_episodes


def execute(*, query: str, limit: int, include_podcasts: bool) -> AgentSearchResponse:
    """Search external providers without changing the main content search semantics."""
    results: list[AgentSearchResultResponse] = []
    web_hits = exa_search(query=query, num_results=limit)
    results.extend(
        AgentSearchResultResponse(
            kind="web",
            title=hit.title,
            url=hit.url,
            snippet=hit.snippet,
            source="exa",
            provider="exa",
            published_at=hit.published_date,
        )
        for hit in web_hits
    )
    if include_podcasts:
        podcast_hits = search_podcast_episodes(query=query, limit=limit)
        results.extend(
            AgentSearchResultResponse(
                kind="podcast",
                title=hit.title,
                url=hit.episode_url,
                snippet=hit.snippet,
                source=hit.source,
                provider=hit.provider,
                feed_url=hit.feed_url,
                published_at=hit.published_at,
                score=hit.score,
            )
            for hit in podcast_hits
        )
    return AgentSearchResponse(results=results[:limit])
