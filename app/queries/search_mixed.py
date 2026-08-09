"""Sectioned search across local content, feeds, and podcasts."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from threading import BoundedSemaphore
from time import monotonic
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.api.content import (
    MixedSearchFeedResultResponse,
    MixedSearchResponse,
    PodcastEpisodeSearchResultResponse,
)
from app.models.internal.scraper_configs import canonicalize_feed_url
from app.queries import search_content_cards
from app.services.assistant_feed_finder import find_feed_options
from app.services.feed_subscription import load_active_feed_urls
from app.services.podcast_search import search_podcast_episodes

logger = get_logger(__name__)
MIXED_SEARCH_EXTERNAL_TIMEOUT_SECONDS = 8.0
MIXED_SEARCH_EXTERNAL_MAX_WORKERS = 4
MIXED_SEARCH_EXTERNAL_ADMISSION_CAPACITY = 4
_EXTERNAL_SEARCH_EXECUTOR = ThreadPoolExecutor(
    max_workers=MIXED_SEARCH_EXTERNAL_MAX_WORKERS,
    thread_name_prefix="mixed-search",
)
_EXTERNAL_SEARCH_SLOTS = BoundedSemaphore(MIXED_SEARCH_EXTERNAL_ADMISSION_CAPACITY)


def _run_external_search(
    search: Callable[..., Any],
    *,
    deadline: float,
    kwargs: dict[str, Any],
) -> Any:
    if monotonic() >= deadline:
        return None
    return search(**kwargs)


def _submit_external_search(
    search: Callable[..., Any],
    *,
    deadline: float,
    kwargs: dict[str, Any],
) -> Future[Any] | None:
    """Submit only when a process-wide external-search admission slot is available."""

    slots = _EXTERNAL_SEARCH_SLOTS
    if monotonic() >= deadline or not slots.acquire(blocking=False):
        return None
    try:
        future = _EXTERNAL_SEARCH_EXECUTOR.submit(
            _run_external_search,
            search,
            deadline=deadline,
            kwargs=kwargs,
        )
        future.add_done_callback(lambda _future: slots.release())
        return future
    except Exception:
        slots.release()
        raise


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
    feed_options = []
    podcast_results = []
    external_deadline = monotonic() + MIXED_SEARCH_EXTERNAL_TIMEOUT_SECONDS
    feed_future = _submit_external_search(
        find_feed_options,
        deadline=external_deadline,
        kwargs={
            "query": query,
            "limit": min(limit, 5),
            "user_id": user_id,
            "deadline": external_deadline,
        },
    )
    podcast_future = _submit_external_search(
        search_podcast_episodes,
        deadline=external_deadline,
        kwargs={"query": query, "limit": limit, "deadline": external_deadline},
    )
    futures = [future for future in (feed_future, podcast_future) if future is not None]
    done, _pending = (
        wait(
            futures,
            timeout=max(0.0, external_deadline - monotonic()),
        )
        if futures
        else (set(), set())
    )
    for section, future in (("feeds", feed_future), ("podcasts", podcast_future)):
        if future is None:
            logger.info(
                "Mixed search external section skipped at concurrency limit",
                extra={
                    "component": "mixed_search",
                    "operation": "external_search",
                    "context_data": {"section": section, "query": query},
                },
            )
            continue
        if future not in done:
            future.cancel()
            logger.warning(
                "Mixed search external section timed out",
                extra={
                    "component": "mixed_search",
                    "operation": "external_search",
                    "context_data": {"section": section, "query": query},
                },
            )
            continue
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001 - one section may degrade independently
            logger.warning(
                "Mixed search external section failed: %s",
                exc,
                extra={
                    "component": "mixed_search",
                    "operation": "external_search",
                    "context_data": {"section": section, "query": query},
                },
            )
            continue
        if result is None:
            continue
        if section == "feeds":
            feed_options = result.options
        else:
            podcast_results = result

    subscribed_feed_urls = load_active_feed_urls(db, user_id=user_id) if feed_options else set()
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
                is_subscribed=canonicalize_feed_url(option.feed_url) in subscribed_feed_urls,
            )
            for option in feed_options
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
