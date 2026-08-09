"""Validated feed discovery for assistant chat turns."""

from __future__ import annotations

from time import monotonic
from urllib.parse import urlparse

from app.models.contracts import FeedFormat, FeedType
from app.models.domain.chat_render import (
    AssistantFeedOption,
    AssistantFeedOptionsResult,
    build_assistant_feed_option_id,
)
from app.models.internal.scraper_configs import canonicalize_feed_url
from app.services.agent_vm_runtime import resolve_sandbox_user_id
from app.services.exa_client import ExaSearchResult, exa_search
from app.services.feed_detection import FeedDetector
from app.services.feed_research_runtime import (
    FeedResearchDeadlineExceeded,
    feed_research_runtime,
)
from app.services.feed_resolution import extract_candidate_feed_urls, resolve_feed_candidate
from app.utils.title_utils import clean_title

MAX_FEED_SEARCH_RESULTS = 8
MAX_FEED_CONTENT_CHARACTERS = 5000
MAX_FEED_OPTIONS = 5
MAX_FEED_OPTION_TITLE_CHARACTERS = 300
MAX_FEED_OPTION_DESCRIPTION_CHARACTERS = 600
MAX_FEED_OPTION_RATIONALE_CHARACTERS = 600
PODCAST_QUERY_HINTS = ("podcast", "podcasts", "episode", "episodes", "show", "shows")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}


def find_feed_options(
    query: str,
    limit: int = MAX_FEED_OPTIONS,
    user_id: int | None = None,
    execution_id: int | None = None,
    deadline: float | None = None,
) -> AssistantFeedOptionsResult:
    """Find validated subscribable feed options for an assistant request."""

    normalized_query = query.strip()
    normalized_limit = max(1, min(limit, MAX_FEED_OPTIONS))
    if not normalized_query or _deadline_expired(deadline):
        return AssistantFeedOptionsResult(query=query, options=[])

    request_timeout_seconds = None if deadline is None else max(0.0, deadline - monotonic())
    search_results = exa_search(
        normalized_query,
        num_results=min(MAX_FEED_SEARCH_RESULTS, max(normalized_limit * 3, normalized_limit)),
        max_characters=MAX_FEED_CONTENT_CHARACTERS,
        telemetry={
            "feature": "assistant_feed_finder",
            "operation": "assistant_feed_finder.search",
            "user_id": user_id,
        },
        request_timeout_seconds=request_timeout_seconds,
    )
    if not search_results or _deadline_expired(deadline):
        return AssistantFeedOptionsResult(query=normalized_query, options=[])
    prefer_site_discovery = _looks_like_podcast_query(normalized_query)

    options: list[AssistantFeedOption] = []
    seen_feed_urls: set[str] = set()
    try:
        with feed_research_runtime(
            user_id=resolve_sandbox_user_id(user_id),
            execution_id=execution_id,
            use_llm=deadline is None,
            deadline=deadline,
        ) as runtime:
            for search_result in search_results:
                if _deadline_expired(deadline):
                    break
                option = _build_option_from_result(
                    search_result=search_result,
                    detector=runtime.detector,
                    seen_feed_urls=seen_feed_urls,
                    prefer_site_discovery=prefer_site_discovery,
                )
                if option is None:
                    continue
                options.append(option)
                if len(options) >= normalized_limit and (
                    not prefer_site_discovery
                    or any(item.feed_type == FeedType.PODCAST_RSS for item in options)
                ):
                    break
    except FeedResearchDeadlineExceeded:
        pass

    ranked_options = _rank_options_for_query(normalized_query, options)
    return AssistantFeedOptionsResult(
        query=normalized_query,
        options=ranked_options[:normalized_limit],
    )


def _deadline_expired(deadline: float | None) -> bool:
    return deadline is not None and monotonic() >= deadline


def _build_option_from_result(
    *,
    search_result: ExaSearchResult,
    detector: FeedDetector,
    seen_feed_urls: set[str],
    prefer_site_discovery: bool,
) -> AssistantFeedOption | None:
    site_url = _normalize_url(search_result.url)
    if site_url is None:
        return None

    if _is_youtube_site_url(site_url):
        live_option = _build_option_from_live_page(
            search_result=search_result,
            site_url=site_url,
            detector=detector,
            seen_feed_urls=seen_feed_urls,
        )
        if live_option is not None:
            return live_option

    page_text = (search_result.snippet or "").strip()

    resolved = resolve_feed_candidate(
        detector=detector,
        title=search_result.title,
        site_url=site_url,
        candidate_feed_urls=extract_candidate_feed_urls(site_url, page_text),
        source="assistant_feed_finder",
        content_type="podcast" if prefer_site_discovery else "article",
        prefer_site_discovery=prefer_site_discovery,
    )
    if resolved is None:
        return None

    return _build_option(
        search_result=search_result,
        site_url=site_url,
        feed_url=resolved["feed_url"],
        feed_format=resolved.get("feed_format", "rss"),
        feed_title=resolved.get("title"),
        detector=detector,
        page_text=page_text,
        seen_feed_urls=seen_feed_urls,
    )


def _build_option_from_live_page(
    *,
    search_result: ExaSearchResult,
    site_url: str,
    detector: FeedDetector,
    seen_feed_urls: set[str],
) -> AssistantFeedOption | None:
    try:
        response = detector.http_service.fetch(site_url)
    except Exception:  # noqa: BLE001
        return None

    resolved = resolve_feed_candidate(
        detector=detector,
        title=search_result.title,
        site_url=str(getattr(response, "url", site_url) or site_url),
        html_content=response.text,
        source="assistant_feed_finder",
    )
    if resolved is None:
        return None

    return _build_option(
        search_result=search_result,
        site_url=site_url,
        feed_url=resolved["feed_url"],
        feed_format=resolved.get("feed_format", "rss"),
        feed_title=resolved.get("title"),
        detector=detector,
        page_text=response.text,
        seen_feed_urls=seen_feed_urls,
    )


def _build_option(
    *,
    search_result: ExaSearchResult,
    site_url: str,
    feed_url: str,
    feed_format: str,
    feed_title: str | None,
    detector: FeedDetector,
    page_text: str,
    seen_feed_urls: set[str],
) -> AssistantFeedOption | None:
    normalized_feed_url = _normalize_feed_url(feed_url)
    if normalized_feed_url is None or normalized_feed_url in seen_feed_urls:
        return None

    classification = detector.classify_feed_type(
        feed_url=normalized_feed_url,
        page_url=site_url,
        page_title=feed_title or search_result.title,
    )
    if classification.feed_type not in {"atom", "substack", "podcast_rss"}:
        return None

    seen_feed_urls.add(normalized_feed_url)
    description = _truncate_text(
        _first_non_empty(search_result.snippet, _truncate_text(page_text, 280)),
        MAX_FEED_OPTION_DESCRIPTION_CHARACTERS,
    )
    title = _truncate_text(
        _first_non_empty(
            clean_title(feed_title),
            clean_title(search_result.title),
            _host_label(site_url),
        ),
        MAX_FEED_OPTION_TITLE_CHARACTERS,
    )
    rationale = _truncate_text(
        classification.reasoning or f"Validated feed for {title or _host_label(site_url)}.",
        MAX_FEED_OPTION_RATIONALE_CHARACTERS,
    )
    return AssistantFeedOption(
        id=build_assistant_feed_option_id(normalized_feed_url),
        title=title or normalized_feed_url,
        site_url=site_url,
        feed_url=normalized_feed_url,
        feed_type=FeedType(classification.feed_type),
        feed_format=FeedFormat.ATOM if str(feed_format).lower() == "atom" else FeedFormat.RSS,
        description=description,
        rationale=rationale,
        evidence_url=site_url,
    )


def _rank_options_for_query(
    query: str,
    options: list[AssistantFeedOption],
) -> list[AssistantFeedOption]:
    if not _looks_like_podcast_query(query):
        return options

    indexed_options = list(enumerate(options))
    ranked = sorted(
        indexed_options,
        key=lambda item: (
            0 if item[1].feed_type == "podcast_rss" else 1,
            item[0],
        ),
    )
    return [option for _, option in ranked]


def _looks_like_podcast_query(query: str) -> bool:
    lowered = query.lower()
    return any(hint in lowered for hint in PODCAST_QUERY_HINTS)


def _is_youtube_site_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host in YOUTUBE_HOSTS


def _normalize_feed_url(feed_url: str) -> str | None:
    normalized = _normalize_url(feed_url)
    if normalized is None:
        return None
    return canonicalize_feed_url(normalized)


def _normalize_url(url: str) -> str | None:
    trimmed = url.strip()
    if not trimmed.startswith(("http://", "https://")):
        return None
    return trimmed.rstrip("/")


def _host_label(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host.removeprefix("www.") or url


def _truncate_text(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    if len(stripped) <= limit:
        return stripped
    return f"{stripped[: limit - 3].rstrip()}..."


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
