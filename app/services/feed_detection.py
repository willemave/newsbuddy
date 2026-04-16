"""RSS/Atom feed detection service.

Detects RSS/Atom feed links in HTML and uses LLM to classify the feed type.
"""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlparse

import feedparser
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.constants import SELF_SUBMISSION_SOURCE
from app.core.logging import get_logger
from app.models.metadata import ContentType
from app.services.exa_client import ExaClientError, exa_search
from app.services.http import HttpService, fetch_quiet_compat, head_quiet_compat
from app.services.llm_agents import get_basic_agent
from app.services.vendor_usage import record_model_usage

logger = get_logger(__name__)

# Configuration
FEED_CLASSIFICATION_MODEL = "openai:gpt-5.4"
FEED_CLASSIFICATION_TIMEOUT = 10.0
FEED_CLASSIFICATION_SYSTEM_PROMPT = (
    "You classify RSS/Atom feeds by inspecting the feed URL and page metadata. "
    "Return structured output that matches the schema."
)
HEURISTIC_CONFIDENCE_THRESHOLD = 0.75
FEED_CANDIDATE_PATHS = (
    "/rss.xml",
    "/feed",
    "/feed/",
    "/feed.xml",
    "/rss",
    "/rss/",
    "/rss/index.xml",
    "/atom",
    "/atom/",
    "/atom.xml",
    "/atom/index.xml",
    "/index.xml",
)
FEED_SECTION_HINTS = (
    "blog",
    "news",
    "posts",
    "articles",
    "podcast",
    "podcasts",
    "updates",
)
FEED_SECTION_SUFFIXES = (
    "/rss.xml",
    "/rss",
    "/rss/",
    "/feed",
    "/feed/",
    "/feed.xml",
    "/atom",
    "/atom/",
    "/atom.xml",
    "/index.xml",
)
FEED_QUERY_PARAMS = (
    "/?feed=rss",
    "/?feed=atom",
)
FEED_ANCHOR_HINTS = (
    "rss",
    "atom",
    "feed",
)
FEED_URL_HINTS = (
    "rss",
    "atom",
    "feed",
    ".xml",
)
FEED_CONTENT_TYPE_HINTS = (
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "text/xml",
    "application/rdf+xml",
)
FEED_DOCUMENT_MARKERS = (
    b"<rss",
    b"<feed",
    b"<rdf:rdf",
)
MAX_FEED_CANDIDATE_FETCHES = 6
MAX_EXA_RESULTS = 5
MAX_EXA_CANDIDATES = 8

SUBSTACK_MARKERS = (
    "substack.com",
    "substackcdn.com",
    "substackcdn",
    "substack.com/api/v1/",
)

PODCAST_HOST_MARKERS = (
    "anchor.fm",
    "transistor.fm",
    "libsyn.com",
    "buzzsprout.com",
    "simplecast.com",
    "captivate.fm",
    "podbean.com",
    "spreaker.com",
    "megaphone.fm",
    "acast.com",
    "rss.com",
    "omny.fm",
    "soundcloud.com",
)

PODCAST_PATH_HINTS = (
    "/podcast",
    "/podcasts",
    "podcast",
    "episode",
    "episodes",
    "audio",
)


class FeedClassificationResult(BaseModel):
    """Structured output schema for feed type classification."""

    feed_type: Literal["substack", "podcast_rss", "atom"] = Field(
        ...,
        description=(
            "Type of feed: 'substack' for Substack newsletters (including custom domains), "
            "'podcast_rss' for podcast feeds with audio episodes, "
            "'atom' for generic blog/news RSS feeds"
        ),
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence score for the classification (0.0-1.0)",
    )
    reasoning: str = Field(
        default="",
        description="Brief explanation for the classification decision",
    )


def _resolve_url(href: str, page_url: str) -> str:
    """Resolve relative URLs against the page URL."""
    if href.startswith(("http://", "https://")):
        return href

    parsed_page = urlparse(page_url)
    if href.startswith("/"):
        return f"{parsed_page.scheme}://{parsed_page.netloc}{href}"

    base_path = parsed_page.path.rsplit("/", 1)[0]
    return f"{parsed_page.scheme}://{parsed_page.netloc}{base_path}/{href}"


def extract_feed_links(html_content: str, page_url: str) -> list[dict[str, str]]:
    """Extract RSS/Atom feed links from HTML content.

    Args:
        html_content: Raw HTML content
        page_url: URL of the page (for resolving relative URLs)

    Returns:
        List of dicts with feed_url, feed_format, and title
    """
    feeds: list[dict[str, str]] = []

    # Pattern to match <link rel="alternate" type="application/rss+xml|atom+xml">
    link_pattern = re.compile(
        r"<link[^>]+rel=[\"']alternate[\"'][^>]*>",
        re.IGNORECASE | re.DOTALL,
    )

    for match in link_pattern.finditer(html_content):
        link_tag = match.group(0)

        # Extract type - must be RSS or Atom
        type_match = re.search(
            r"type=[\"']application/(rss\+xml|atom\+xml)[\"']",
            link_tag,
            re.IGNORECASE,
        )
        if not type_match:
            continue

        feed_format = "rss" if "rss" in type_match.group(1).lower() else "atom"

        # Extract href
        href_match = re.search(r"href=[\"']([^\"']+)[\"']", link_tag, re.IGNORECASE)
        if not href_match:
            continue

        feed_url = _resolve_url(href_match.group(1), page_url)

        # Extract title
        title_match = re.search(r"title=[\"']([^\"']*)[\"']", link_tag, re.IGNORECASE)
        title = title_match.group(1) if title_match else None

        feeds.append(
            {
                "feed_url": feed_url,
                "feed_format": feed_format,
                "title": title or "",
            }
        )

    return feeds


def extract_feed_links_from_anchors(html_content: str, page_url: str) -> list[dict[str, str]]:
    """Extract RSS/Atom feed links from anchor tags in HTML content."""
    feeds: list[dict[str, str]] = []
    anchor_pattern = re.compile(
        r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        re.IGNORECASE | re.DOTALL,
    )

    for match in anchor_pattern.finditer(html_content):
        href = match.group(1).strip()
        if not href or href.startswith(("javascript:", "mailto:")):
            continue

        anchor_text_raw = re.sub(r"<[^>]+>", "", match.group(2) or "").strip()
        combined = f"{href} {anchor_text_raw}".lower()
        if not any(hint in combined for hint in FEED_ANCHOR_HINTS):
            continue

        feed_url = _resolve_url(href, page_url)
        feed_format = "atom" if "atom" in combined else "rss"

        feeds.append(
            {
                "feed_url": feed_url,
                "feed_format": feed_format,
                "title": anchor_text_raw or "",
            }
        )

    return feeds


def _extract_canonical_page_urls(html_content: str, page_url: str) -> list[str]:
    """Extract canonical page URLs that may expose safer feed candidates."""
    candidates = [page_url]
    patterns = (
        r"<link[^>]+rel=[\"']canonical[\"'][^>]*href=[\"']([^\"']+)[\"']",
        r"<meta[^>]+property=[\"']og:url[\"'][^>]*content=[\"']([^\"']+)[\"']",
        r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]*property=[\"']og:url[\"']",
    )

    for pattern in patterns:
        for match in re.finditer(pattern, html_content, re.IGNORECASE | re.DOTALL):
            href = (match.group(1) or "").strip()
            if not href:
                continue
            candidates.append(_resolve_url(href, page_url))

    return list(dict.fromkeys(candidates))


def _looks_like_feed_url(url: str) -> bool:
    lowered = url.lower()
    return any(hint in lowered for hint in FEED_URL_HINTS)


def _extract_candidate_section_paths(path: str) -> list[str]:
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return []

    section = segments[0].lower()
    if section.isdigit():
        return []
    if section in FEED_SECTION_HINTS or len(section) >= 3:
        return [f"/{section}"]
    return []


def _build_candidate_feed_urls(page_url: str) -> list[str]:
    parsed = urlparse(page_url)
    if not parsed.scheme or not parsed.netloc:
        return []

    base = f"{parsed.scheme}://{parsed.netloc}"
    candidates: list[str] = []

    for suffix in FEED_CANDIDATE_PATHS:
        candidates.append(f"{base}{suffix}")
    for query in FEED_QUERY_PARAMS:
        candidates.append(f"{base}{query}")

    for section_path in _extract_candidate_section_paths(parsed.path):
        for suffix in FEED_SECTION_SUFFIXES:
            candidates.append(f"{base}{section_path}{suffix}")

    return list(dict.fromkeys(candidates))


def _infer_feed_format(
    parsed_feed: Any,
    content_type: str | None,
    content: bytes,
) -> str:
    content_type_value = (content_type or "").lower()
    if "atom" in content_type_value:
        return "atom"
    if "rss" in content_type_value:
        return "rss"

    version = str(getattr(parsed_feed, "version", "") or "").lower()
    if "atom" in version:
        return "atom"

    head = content[:2000].lower()
    if b"<feed" in head:
        return "atom"
    return "rss"


def _looks_like_feed_document(
    parsed_feed: Any,
    content_type: str | None,
    content: bytes,
) -> bool:
    """Return True when the fetched payload appears to be a real feed document."""
    content_type_value = (content_type or "").lower()
    if any(hint in content_type_value for hint in FEED_CONTENT_TYPE_HINTS):
        return True

    version = str(getattr(parsed_feed, "version", "") or "").lower()
    if "rss" in version or "atom" in version:
        return True

    head = content[:2000].lower()
    return any(marker in head for marker in FEED_DOCUMENT_MARKERS)


def _extract_urls_from_text(text: str) -> list[str]:
    if not text:
        return []
    return re.findall(r"https?://[^\s\"'<>]+", text)


def classify_feed_type_with_llm(
    feed_url: str,
    page_url: str,
    page_title: str | None,
    model_spec: str | None = None,
    *,
    db: Session | None = None,
    usage_persist: dict[str, Any] | None = None,
) -> FeedClassificationResult | None:
    """Use LLM to classify the feed type.

    Args:
        feed_url: The RSS/Atom feed URL
        page_url: The original page URL where the feed was found
        page_title: Title of the page (if available)

    Returns:
        FeedClassificationResult on success, None on failure
    """
    try:
        prompt = _build_classification_prompt(feed_url, page_url, page_title)
        agent = get_basic_agent(
            model_spec=model_spec or FEED_CLASSIFICATION_MODEL,
            output_type=FeedClassificationResult,
            system_prompt=FEED_CLASSIFICATION_SYSTEM_PROMPT,
        )
        run_result = agent.run_sync(
            prompt,
            model_settings={"timeout": FEED_CLASSIFICATION_TIMEOUT},
        )
        record_model_usage(
            "feed_classification",
            run_result,
            model_spec=model_spec or FEED_CLASSIFICATION_MODEL,
            persist=usage_persist,
        )
        result = run_result.output

        logger.info(
            "Feed classified: type=%s, confidence=%.2f",
            result.feed_type,
            result.confidence,
            extra={
                "component": "feed_detection",
                "operation": "classify_feed_type",
                "context_data": {
                    "feed_url": feed_url,
                    "page_url": page_url,
                    "feed_type": result.feed_type,
                    "confidence": result.confidence,
                },
            },
        )
        return result

    except ValueError as e:
        logger.warning(
            "LLM configuration error during feed classification: %s",
            e,
            extra={
                "component": "feed_detection",
                "operation": "classify_feed_type",
                "context_data": {"feed_url": feed_url, "error": str(e)},
            },
        )
        return None

    except Exception as e:  # noqa: BLE001
        logger.exception(
            "Unexpected error during feed classification: %s",
            e,
            extra={
                "component": "feed_detection",
                "operation": "classify_feed_type",
                "context_data": {"feed_url": feed_url, "error": str(e)},
            },
        )
        return None


def _build_classification_prompt(
    feed_url: str,
    page_url: str,
    page_title: str | None,
) -> str:
    """Build the classification prompt for the LLM."""
    title_line = f"Page Title: {page_title}\n" if page_title else ""

    return f"""Classify this RSS/Atom feed based on the feed URL and the page it was found on.

Feed URL: {feed_url}
Page URL: {page_url}
{title_line}
Classify as one of:
- "substack": Substack newsletter. Substack publications may use custom domains
  (e.g., chinatalk.media, stratechery.com) but are still Substack-powered.
  Look for substack.com in the feed URL, or indicators that this is a newsletter.
- "podcast_rss": Podcast feed with audio episodes. Look for podcast hosting platforms
  (anchor.fm, transistor.fm, libsyn, buzzsprout, simplecast, captivate, podbean, spreaker)
  or keywords like podcast/episode in the URL.
- "atom": Generic blog or news RSS feed that doesn't fit the above categories.

Return your classification with confidence score and brief reasoning."""


def _normalize_value(value: str | None) -> str:
    return (value or "").strip().lower()


def _is_substack_feed(feed_url: str, page_url: str, html_content: str | None) -> bool:
    normalized_feed = _normalize_value(feed_url)
    normalized_page = _normalize_value(page_url)
    if any(marker in normalized_feed for marker in SUBSTACK_MARKERS):
        return True
    if any(marker in normalized_page for marker in SUBSTACK_MARKERS):
        return True
    if html_content:
        normalized_html = html_content.lower()
        if "substack" in normalized_html:
            return True
    return False


def _is_podcast_feed(feed_url: str) -> bool:
    normalized_feed = _normalize_value(feed_url)
    return any(marker in normalized_feed for marker in PODCAST_HOST_MARKERS) or any(
        hint in normalized_feed for hint in PODCAST_PATH_HINTS
    )


def _classify_feed_type_heuristic(
    feed_url: str,
    page_url: str,
    page_title: str | None,
    html_content: str | None,
) -> FeedClassificationResult:
    if _is_substack_feed(feed_url, page_url, html_content):
        return FeedClassificationResult(
            feed_type="substack",
            confidence=0.9,
            reasoning="Detected Substack markers in URL or HTML.",
        )
    if _is_podcast_feed(feed_url):
        return FeedClassificationResult(
            feed_type="podcast_rss",
            confidence=0.8,
            reasoning="Matched known podcast host or URL keyword.",
        )
    return FeedClassificationResult(
        feed_type="atom",
        confidence=0.4,
        reasoning=("No strong Substack or podcast indicators; defaulting to generic RSS/Atom."),
    )


def _should_detect_feed(source: str | None, content_type: ContentType | str | None) -> bool:
    if source == SELF_SUBMISSION_SOURCE:
        return True
    if content_type is None:
        return False
    normalized_type = (
        content_type.value if isinstance(content_type, ContentType) else str(content_type)
    )
    return normalized_type == ContentType.NEWS.value


class FeedDetector:
    """Unified feed detection with heuristic + optional LLM fallback."""

    def __init__(
        self,
        *,
        use_llm: bool = True,
        use_exa_search: bool = True,
        http_service: HttpService | None = None,
        max_candidate_fetches: int = MAX_FEED_CANDIDATE_FETCHES,
    ):
        self.use_llm = use_llm
        self.use_exa_search = use_exa_search
        self.http_service = http_service or HttpService()
        self.max_candidate_fetches = max_candidate_fetches

    def classify_feed_type(
        self,
        feed_url: str,
        page_url: str,
        page_title: str | None,
        html_content: str | None = None,
        *,
        model_spec: str | None = None,
        db: Session | None = None,
        usage_persist: dict[str, Any] | None = None,
    ) -> FeedClassificationResult:
        heuristic = _classify_feed_type_heuristic(
            feed_url,
            page_url,
            page_title,
            html_content,
        )
        if heuristic.confidence >= HEURISTIC_CONFIDENCE_THRESHOLD or not self.use_llm:
            return heuristic

        llm_result = classify_feed_type_with_llm(
            feed_url=feed_url,
            page_url=page_url,
            page_title=page_title,
            model_spec=model_spec,
            db=db,
            usage_persist=usage_persist,
        )
        return llm_result or heuristic

    def _validate_feed_candidate(self, feed_url: str) -> dict[str, str] | None:
        try:
            head_response = head_quiet_compat(
                self.http_service,
                feed_url,
                allow_statuses={405},
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "Feed candidate HEAD failed: %s",
                e,
                extra={
                    "component": "feed_detection",
                    "operation": "validate_feed_candidate_head",
                    "context_data": {"feed_url": feed_url, "error": str(e)},
                },
            )
            return None

        if head_response.status_code >= 400 and head_response.status_code != 405:
            return None

        if head_response.status_code == 405:
            logger.debug(
                "HEAD not allowed for %s, falling back to GET",
                feed_url,
                extra={
                    "component": "feed_detection",
                    "operation": "validate_feed_candidate_head",
                    "context_data": {"feed_url": feed_url, "status_code": 405},
                },
            )

        try:
            response = fetch_quiet_compat(self.http_service, feed_url)
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "Feed candidate fetch failed: %s",
                e,
                extra={
                    "component": "feed_detection",
                    "operation": "validate_feed_candidate",
                    "context_data": {"feed_url": feed_url, "error": str(e)},
                },
            )
            return None

        parsed_feed = feedparser.parse(response.content)
        if parsed_feed.bozo and not parsed_feed.entries and not getattr(parsed_feed, "feed", None):
            return None
        if not _looks_like_feed_document(
            parsed_feed,
            response.headers.get("content-type"),
            response.content,
        ):
            return None

        title = None
        if getattr(parsed_feed, "feed", None):
            title = parsed_feed.feed.get("title")

        feed_format = _infer_feed_format(
            parsed_feed,
            response.headers.get("content-type"),
            response.content,
        )

        logger.info(
            "Validated feed candidate %s",
            feed_url,
            extra={
                "component": "feed_detection",
                "operation": "validate_feed_candidate",
                "context_data": {"feed_url": feed_url, "feed_format": feed_format},
            },
        )

        normalized_feed_url = str(getattr(response, "url", feed_url) or feed_url)

        return {
            "feed_url": normalized_feed_url,
            "feed_format": feed_format,
            "title": title or "",
        }

    def validate_feed_url(self, feed_url: str) -> dict[str, str] | None:
        """Validate a feed URL and return metadata if it looks like a real feed."""
        return self._validate_feed_candidate(feed_url)

    def _validate_feed_candidates(self, feed_urls: list[str]) -> list[dict[str, str]]:
        validated: list[dict[str, str]] = []
        for feed_url in feed_urls[: self.max_candidate_fetches]:
            result = self._validate_feed_candidate(feed_url)
            if result:
                validated.append(result)
                break
        return validated

    def _validate_feed_links(self, feed_links: list[dict[str, str]]) -> list[dict[str, str]]:
        validated: list[dict[str, str]] = []
        for feed_link in feed_links[: self.max_candidate_fetches]:
            feed_url = feed_link.get("feed_url")
            if not isinstance(feed_url, str) or not feed_url.strip():
                continue
            result = self._validate_feed_candidate(feed_url.strip())
            if not result:
                continue
            validated.append(
                {
                    "feed_url": result["feed_url"],
                    "feed_format": feed_link.get("feed_format") or result["feed_format"],
                    "title": feed_link.get("title") or result.get("title") or "",
                }
            )
            break
        return validated

    def _find_feed_candidates_via_exa(self, page_url: str) -> list[str]:
        parsed = urlparse(page_url)
        domain = parsed.netloc
        if not domain:
            return []

        query = f"site:{domain} rss feed"
        try:
            results = exa_search(
                query,
                num_results=MAX_EXA_RESULTS,
                include_domains=[domain],
                raise_on_error=True,
            )
        except ExaClientError as exc:
            logger.warning(
                "Feed candidate Exa search failed for %s",
                page_url,
                extra={
                    "component": "feed_detection",
                    "operation": "find_feed_candidates_via_exa",
                    "context_data": {
                        "page_url": page_url,
                        "query": query,
                        "error": str(exc),
                    },
                },
            )
            return []

        candidates: list[str] = []
        for result in results:
            if _looks_like_feed_url(result.url):
                candidates.append(result.url)
            candidates.extend(
                [
                    url
                    for url in _extract_urls_from_text(result.snippet or "")
                    if _looks_like_feed_url(url)
                ]
            )

        return list(dict.fromkeys(candidates))[:MAX_EXA_CANDIDATES]

    def _discover_feed_links(
        self,
        page_url: str,
        html_content: str | None,
    ) -> list[dict[str, str]]:
        if html_content:
            feed_links = extract_feed_links(html_content, page_url)
            validated_explicit_feeds = self._validate_feed_links(feed_links)
            if validated_explicit_feeds:
                return validated_explicit_feeds

        candidate_urls: list[str] = []
        candidate_page_urls = [page_url]
        if html_content:
            candidate_page_urls = _extract_canonical_page_urls(html_content, page_url)
        for candidate_page_url in candidate_page_urls:
            candidate_urls.extend(_build_candidate_feed_urls(candidate_page_url))
        candidate_urls = list(dict.fromkeys(candidate_urls))
        candidate_feeds = self._validate_feed_candidates(candidate_urls)
        if candidate_feeds:
            return candidate_feeds

        if html_content:
            anchor_feeds = extract_feed_links_from_anchors(html_content, page_url)
            validated_anchor_feeds = self._validate_feed_links(anchor_feeds)
            if validated_anchor_feeds:
                return validated_anchor_feeds

        if self.use_exa_search:
            exa_candidates = self._find_feed_candidates_via_exa(page_url)
            exa_feeds = self._validate_feed_candidates(exa_candidates)
            if exa_feeds:
                return exa_feeds

        return []

    def detect_from_html(
        self,
        html_content: str,
        page_url: str,
        page_title: str | None = None,
        *,
        source: str | None = None,
        content_type: ContentType | str | None = None,
        model_spec: str | None = None,
        force_detect: bool = False,
        db: Session | None = None,
        usage_persist: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not force_detect and not _should_detect_feed(source, content_type):
            return None

        feeds = extract_feed_links(html_content, page_url)
        return self.detect_from_links(
            feeds,
            page_url,
            page_title=page_title,
            html_content=html_content,
            source=source,
            content_type=content_type,
            model_spec=model_spec,
            force_detect=force_detect,
            db=db,
            usage_persist=usage_persist,
        )

    def detect_from_links(
        self,
        feed_links: list[dict[str, str]] | None,
        page_url: str,
        page_title: str | None = None,
        html_content: str | None = None,
        *,
        source: str | None = None,
        content_type: ContentType | str | None = None,
        model_spec: str | None = None,
        force_detect: bool = False,
        db: Session | None = None,
        usage_persist: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not force_detect and not _should_detect_feed(source, content_type):
            return None

        if not feed_links:
            feed_links = self._discover_feed_links(page_url, html_content)
            if not feed_links:
                return None

        primary_feed = feed_links[0]
        classification = self.classify_feed_type(
            feed_url=primary_feed["feed_url"],
            page_url=page_url,
            page_title=page_title,
            html_content=html_content,
            model_spec=model_spec,
            db=db,
            usage_persist=usage_persist,
        )
        feed_type = classification.feed_type

        return {
            "detected_feed": {
                "url": primary_feed["feed_url"],
                "type": feed_type,
                "title": primary_feed.get("title"),
                "format": primary_feed.get("feed_format", "rss"),
            },
            "all_detected_feeds": feed_links if len(feed_links) > 1 else None,
        }


def detect_feeds_from_html(
    html_content: str,
    page_url: str,
    page_title: str | None = None,
    source: str | None = None,
    content_type: ContentType | str | None = None,
    *,
    db: Session | None = None,
    usage_persist: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Detect feeds from HTML and return metadata for storage.

    Only processes self-submitted or news content.

    Args:
        html_content: Raw HTML content
        page_url: URL of the page
        page_title: Title of the page (if available)
        source: Content source (e.g., "self submission")
        content_type: Content type (used to allow news detection)

    Returns:
        Dict with detected_feed info, or None if no feed found or not applicable
    """
    detector = FeedDetector()
    return detector.detect_from_html(
        html_content,
        page_url,
        page_title=page_title,
        source=source,
        content_type=content_type,
        db=db,
        usage_persist=usage_persist,
    )
