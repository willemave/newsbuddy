"""Validated feed discovery for assistant chat turns."""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from app.models.chat_message_metadata import (
    AssistantFeedOption,
    AssistantFeedOptionsResult,
    build_assistant_feed_option_id,
)
from app.services.exa_client import ExaContentResult, ExaSearchResult, exa_get_contents, exa_search
from app.services.feed_detection import FeedDetector

MAX_FEED_SEARCH_RESULTS = 8
MAX_FEED_CONTENT_CHARACTERS = 5000
MAX_FEED_OPTIONS = 5
MAX_FEED_OPTION_TITLE_CHARACTERS = 300
MAX_FEED_OPTION_DESCRIPTION_CHARACTERS = 600
MAX_FEED_OPTION_RATIONALE_CHARACTERS = 600
URL_REGEX = r"https?://[^\s\"'<>]+"
URL_TRIM_CHARS = ".,);]>'\""


def find_feed_options(query: str, limit: int = MAX_FEED_OPTIONS) -> AssistantFeedOptionsResult:
    """Find validated subscribable feed options for an assistant request."""

    normalized_query = query.strip()
    normalized_limit = max(1, min(limit, MAX_FEED_OPTIONS))
    if not normalized_query:
        return AssistantFeedOptionsResult(query=query, options=[])

    search_results = exa_search(
        normalized_query,
        num_results=min(MAX_FEED_SEARCH_RESULTS, max(normalized_limit * 3, normalized_limit)),
        max_characters=1200,
    )
    content_by_url = _content_results_by_url(search_results)
    detector = FeedDetector(use_exa_search=True, use_llm=True)

    options: list[AssistantFeedOption] = []
    seen_feed_urls: set[str] = set()

    for search_result in search_results:
        option = _build_option_from_result(
            search_result=search_result,
            content_result=content_by_url.get(search_result.url),
            detector=detector,
            seen_feed_urls=seen_feed_urls,
        )
        if option is None:
            continue
        options.append(option)
        if len(options) >= normalized_limit:
            break

    return AssistantFeedOptionsResult(query=normalized_query, options=options)


def _content_results_by_url(
    search_results: list[ExaSearchResult],
) -> dict[str, ExaContentResult]:
    urls = [result.url for result in search_results if result.url]
    content_results = exa_get_contents(urls, max_characters=MAX_FEED_CONTENT_CHARACTERS)
    return {result.url: result for result in content_results if result.url}


def _build_option_from_result(
    *,
    search_result: ExaSearchResult,
    content_result: ExaContentResult | None,
    detector: FeedDetector,
    seen_feed_urls: set[str],
) -> AssistantFeedOption | None:
    site_url = _normalize_url(search_result.url)
    if site_url is None:
        return None

    page_text = "\n".join(
        part.strip()
        for part in (
            content_result.text if content_result else None,
            content_result.summary if content_result else None,
            search_result.snippet,
        )
        if isinstance(part, str) and part.strip()
    )

    for candidate_feed_url in _candidate_feed_urls(site_url, page_text):
        validated = detector.validate_feed_url(candidate_feed_url)
        if not validated:
            continue
        option = _build_option(
            search_result=search_result,
            site_url=site_url,
            feed_url=validated["feed_url"],
            feed_format=validated.get("feed_format", "rss"),
            feed_title=validated.get("title"),
            detector=detector,
            page_text=page_text,
            seen_feed_urls=seen_feed_urls,
        )
        if option is not None:
            return option

    detection = detector.detect_from_links(
        None,
        page_url=site_url,
        page_title=search_result.title,
        source="assistant_feed_finder",
        content_type="article",
        force_detect=True,
    )
    if not detection:
        return None

    detected_feed = detection["detected_feed"]
    return _build_option(
        search_result=search_result,
        site_url=site_url,
        feed_url=detected_feed["url"],
        feed_format=detected_feed.get("format", "rss"),
        feed_title=detected_feed.get("title"),
        detector=detector,
        page_text=page_text,
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
        _first_non_empty(feed_title, search_result.title, _host_label(site_url)),
        MAX_FEED_OPTION_TITLE_CHARACTERS,
    )
    rationale = _truncate_text(
        classification.reasoning
        or f"Validated feed for {title or _host_label(site_url)}.",
        MAX_FEED_OPTION_RATIONALE_CHARACTERS,
    )
    return AssistantFeedOption(
        id=build_assistant_feed_option_id(normalized_feed_url),
        title=title or normalized_feed_url,
        site_url=site_url,
        feed_url=normalized_feed_url,
        feed_type=classification.feed_type,
        feed_format="atom" if str(feed_format).lower() == "atom" else "rss",
        description=description,
        rationale=rationale,
        evidence_url=site_url,
    )


def _candidate_feed_urls(site_url: str, page_text: str) -> list[str]:
    urls: list[str] = []
    if _looks_like_feed_url(site_url):
        urls.append(site_url)

    for raw_url in _extract_urls_from_text(page_text):
        if _looks_like_feed_url(raw_url):
            urls.append(raw_url)

    return list(dict.fromkeys(urls))


def _extract_urls_from_text(text: str) -> list[str]:
    if not text:
        return []

    import re

    return [match.rstrip(URL_TRIM_CHARS) for match in re.findall(URL_REGEX, text)]


def _looks_like_feed_url(url: str) -> bool:
    lowered = url.lower()
    return any(hint in lowered for hint in ("rss", "atom", "feed", ".xml"))


def _normalize_feed_url(feed_url: str) -> str | None:
    normalized = _normalize_url(feed_url)
    if normalized is None:
        return None

    parsed = urlparse(normalized)
    path = parsed.path.rstrip("/") or parsed.path
    normalized_parts = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path,
    )
    return urlunparse(normalized_parts)


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
