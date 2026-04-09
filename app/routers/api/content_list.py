"""Content listing and search endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_readonly_db_session
from app.core.deps import get_current_user
from app.models.user import User
from app.queries import list_content_cards, search_content_cards
from app.models.api.common import (
    ContentListResponse,
    MixedSearchFeedResultResponse,
    MixedSearchResponse,
    PodcastEpisodeSearchResponse,
    PodcastEpisodeSearchResultResponse,
)
from app.services.assistant_feed_finder import find_feed_options
from app.services.podcast_search import search_podcast_episodes

router = APIRouter()


@router.get(
    "/",
    response_model=ContentListResponse,
    summary="List content items",
    description=(
        "Retrieve a list of content items with optional filtering by content type and date. "
        "Supports multiple content types via repeated query parameters "
        "(e.g., ?content_type=article&content_type=podcast). "
        "Supports cursor-based pagination for efficient loading."
    ),
)
def list_contents(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    content_type: Annotated[
        list[str] | None,
        Query(
            description=(
                "Filter by content type(s). Can be specified multiple times "
                "for multiple types (article/podcast/news)"
            ),
        ),
    ] = None,
    date: Annotated[
        str | None,
        Query(description="Filter by date (YYYY-MM-DD format)", pattern="^\\d{4}-\\d{2}-\\d{2}$"),
    ] = None,
    read_filter: Annotated[
        str,
        Query(
            description="Filter by read status (all/read/unread)",
            pattern="^(all|read|unread)$",
        ),
    ] = "all",
    cursor: Annotated[str | None, Query(description="Pagination cursor for next page")] = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=100,
            description="Number of items per page (max 100)",
        ),
    ] = 25,
    include_available_dates: Annotated[
        bool,
        Query(
            description=(
                "Whether to include the available_dates metadata used by filterable list UIs. "
                "Disable for feed surfaces that do not show date filters."
            ),
        ),
    ] = True,
) -> ContentListResponse:
    """List content with optional filters and cursor-based pagination."""
    try:
        return list_content_cards.execute(
            db,
            user_id=current_user.id,
            content_type=content_type,
            date=date,
            read_filter=read_filter,
            cursor=cursor,
            limit=limit,
            include_available_dates=include_available_dates,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date format") from exc


@router.get(
    "/search",
    response_model=ContentListResponse,
    summary="Search content across articles and podcasts",
    description=(
        "Case-insensitive string search across titles, sources, and summaries. "
        "Results exclude items classified as 'skip' and only include summarized content. "
        "Supports cursor-based pagination for efficient loading."
    ),
)
def search_contents(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    q: str = Query(
        ..., min_length=2, max_length=200, description="Search query (min 2 characters)"
    ),
    type: str = Query(
        "all",
        pattern=r"^(all|article|podcast|news)$",
        description="Optional content type filter",
    ),
    limit: int = Query(25, ge=1, le=100, description="Max results to return"),
    cursor: str | None = Query(None, description="Pagination cursor for next page"),
    offset: int = Query(
        0,
        ge=0,
        description="Results offset for pagination (deprecated, use cursor instead)",
        deprecated=True,
    ),
) -> ContentListResponse:
    """Search content with portable SQL patterns and cursor-based pagination."""
    return search_content_cards.execute(
        db,
        user_id=current_user.id,
        q=q,
        content_type=type,
        limit=limit,
        cursor=cursor,
        offset=offset,
    )


@router.get(
    "/search/mixed",
    response_model=MixedSearchResponse,
    summary="Search content, feeds, and podcasts in sectioned form",
    description=(
        "Explicit-submit mixed search used by the app Search screen. "
        "Returns local content matches plus external feed/source and podcast sections."
    ),
)
def search_mixed_contents(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    q: str = Query(
        ..., min_length=2, max_length=200, description="Search query (min 2 characters)"
    ),
    limit: int = Query(10, ge=1, le=25, description="Max results per section"),
) -> MixedSearchResponse:
    """Search local content plus external feed/source and podcast sections."""
    local_results = search_content_cards.execute(
        db,
        user_id=current_user.id,
        q=q,
        content_type="all",
        limit=limit,
        cursor=None,
        offset=0,
    )
    feed_results = find_feed_options(query=q, limit=min(limit, 5))
    podcast_results = search_podcast_episodes(query=q, limit=limit)

    return MixedSearchResponse(
        query=q,
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


@router.get(
    "/search/podcasts",
    response_model=PodcastEpisodeSearchResponse,
    summary="Search for podcast episodes across the web",
    description=(
        "Search external podcast episode pages and return addable episode URLs. "
        "Uses the same online discovery search infrastructure with provider fallbacks."
    ),
)
def search_podcast_episode_matches(
    current_user: Annotated[User, Depends(get_current_user)],
    q: str = Query(
        ..., min_length=2, max_length=200, description="Podcast search query (min 2 characters)"
    ),
    limit: int = Query(10, ge=1, le=25, description="Max episode matches to return"),
) -> PodcastEpisodeSearchResponse:
    """Search external podcast episodes for direct add-to-inbox flows."""
    del current_user  # Require auth for parity with other content search endpoints.

    results = search_podcast_episodes(query=q, limit=limit)
    return PodcastEpisodeSearchResponse(
        results=[
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
            for result in results
        ]
    )
