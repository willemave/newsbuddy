"""Sectioned search across local content, feeds, and podcasts."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.api.common import (
    MixedSearchFeedResultResponse,
    MixedSearchResponse,
    PodcastEpisodeSearchResultResponse,
)
from app.queries import search_content_cards
from app.services.assistant_feed_finder import find_feed_options
from app.services.podcast_search import search_podcast_episodes


def execute(db: Session, *, user_id: int, query: str, limit: int) -> MixedSearchResponse:
    """Search local content plus external feed/source and podcast sections."""
    local_results = search_content_cards.execute(
        db,
        user_id=user_id,
        q=query,
        content_type="all",
        limit=limit,
        cursor=None,
        offset=0,
    )
    feed_results = find_feed_options(query=query, limit=min(limit, 5))
    podcast_results = search_podcast_episodes(query=query, limit=limit)

    return MixedSearchResponse(
        query=query,
        content=local_results.contents,
        feeds=[
            MixedSearchFeedResultResponse(
                id=option.id,
                title=option.title,
                site_url=option.site_url,
                feed_url=option.feed_url,
                feed_type=option.feed_type,
                feed_format=option.feed_format,
                description=option.description,
                rationale=option.rationale,
                evidence_url=option.evidence_url,
            )
            for option in feed_results.options
        ],
        podcasts=[
            PodcastEpisodeSearchResultResponse(
                title=result.title,
                episode_url=result.episode_url,
                podcast_title=result.podcast_title,
                source=result.source,
                snippet=result.snippet,
                feed_url=result.feed_url,
                published_at=result.published_at,
                provider=result.provider,
                score=result.score,
            )
            for result in podcast_results
        ],
    )
