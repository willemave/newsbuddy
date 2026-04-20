"""Exa search client service for chat agent web search tool."""

from dataclasses import dataclass
from typing import Any, Literal

from exa_py import Exa

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.vendor_costs import record_vendor_usage_out_of_band

logger = get_logger(__name__)

_exa_client: Exa | None = None


class ExaClientError(RuntimeError):
    """Base exception for Exa client failures."""


class ExaUnavailableError(ExaClientError):
    """Raised when Exa is required but not configured."""


class ExaRequestError(ExaClientError):
    """Raised when an Exa request fails."""


def get_exa_client() -> Exa | None:
    """Get singleton Exa client instance.

    Returns:
        Exa client if API key is configured, None otherwise.
    """
    global _exa_client

    if _exa_client is not None:
        return _exa_client

    settings = get_settings()
    if not settings.exa_api_key:
        logger.warning("Exa API key not configured, web search will be unavailable")
        return None

    _exa_client = Exa(api_key=settings.exa_api_key)
    logger.info("Initialized Exa client for web search")
    return _exa_client


@dataclass
class ExaSearchResult:
    """A single search result from Exa."""

    title: str
    url: str
    snippet: str | None = None
    published_date: str | None = None


@dataclass
class ExaContentResult:
    """Fetched Exa page content for a URL."""

    title: str
    url: str
    text: str | None = None
    summary: str | None = None
    published_date: str | None = None


def _extract_clean_snippet(text: str, max_chars: int) -> str:
    """Extract a clean snippet from text, skipping navigation cruft.

    Tries to find the start of actual content by looking for headers,
    paragraphs, or skipping lines that look like navigation.

    Args:
        text: Raw text content (usually markdown).
        max_chars: Maximum characters to return.

    Returns:
        Cleaned snippet string.
    """
    if not text:
        return ""

    lines = text.split("\n")
    content_start = 0

    # Patterns that indicate navigation/boilerplate (lowercase matching)
    nav_patterns = [
        "skip to",
        "[…]",
        "→",
        "navigation",
        "menu",
        "sign in",
        "log in",
        "subscribe",
        "ctrl k",  # Search shortcuts
        "ctrl+k",
        "⌘k",
        "join us",
        "copy link",
        "share",
        "email",
        "cookie",
    ]

    for i, line in enumerate(lines):
        line_lower = line.lower().strip()
        line_stripped = line.strip()

        # Skip empty lines
        if len(line_stripped) == 0:
            continue

        # Check if line looks like navigation
        is_nav = any(pattern in line_lower for pattern in nav_patterns)

        # Check if line is just a short markdown link (category/breadcrumb)
        is_short_link = (line_stripped.startswith("- [") and len(line_stripped) < 80) or (
            line_stripped.startswith("[") and "](" in line_stripped and len(line_stripped) < 60
        )

        # Check if line is just a keyboard shortcut or very short
        is_shortcut = len(line_stripped) < 10 and not line_stripped.startswith("#")

        # Check if we found real content
        is_header = line_stripped.startswith("#") and len(line_stripped) > 5
        is_substantial = len(line_stripped) > 60 and not is_nav and not is_short_link

        if is_header or is_substantial:
            content_start = i
            break

        # If none of the skip conditions match, this might be content
        if not (is_nav or is_short_link or is_shortcut):
            content_start = i
            break

    # Join from content start and truncate
    clean_text = "\n".join(lines[content_start:])
    return clean_text[:max_chars].strip()


# Domains that are often paywalled, login-gated, or return useless content
EXCLUDED_DOMAINS = [
    "facebook.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "tiktok.com",
    "pinterest.com",
    "reddit.com",  # Often requires login for full content
]


def exa_search(
    query: str,
    num_results: int = 5,
    max_characters: int = 2000,
    category: str | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    raise_on_error: bool = False,
    telemetry: dict[str, Any] | None = None,
) -> list[ExaSearchResult]:
    """Search the web using Exa and return results with full content.

    Uses search_and_contents with:
    - livecrawl="fallback" to get fresh content when cached unavailable
    - summary generation for AI-powered snippets
    - text fallback for when summary unavailable

    Args:
        query: Search query string.
        num_results: Maximum number of results to return.
        max_characters: Maximum characters to include from each result's text.
        category: Optional category filter (e.g., 'news', 'research paper', 'company').
        include_domains: List of domains to include (overrides excludes if set).
        exclude_domains: List of domains to exclude (defaults to social media).
        raise_on_error: When True, raise Exa-specific exceptions instead of returning [].

    Returns:
        List of ExaSearchResult objects with title, url, and snippet.

    Raises:
        ExaUnavailableError: When Exa is not configured and ``raise_on_error=True``.
        ExaRequestError: When the Exa API request fails and ``raise_on_error=True``.
    """
    client = get_exa_client()
    if client is None:
        message = "Exa client not available"
        if raise_on_error:
            logger.error(message)
            raise ExaUnavailableError(message)
        logger.warning("%s; returning empty results", message)
        return []

    # Build exclude list - use provided or default
    effective_excludes = exclude_domains if exclude_domains is not None else EXCLUDED_DOMAINS

    try:
        logger.info(f"[Exa] Starting search | query='{query[:100]}'")

        # Build contents options (livecrawl, summary, text go here)
        contents_opts: dict = {
            "livecrawl": "fallback",  # Use live crawling if cached content unavailable
            "summary": {"query": "Key points and main takeaways"},  # Get AI summary
            "text": {"max_characters": max_characters},  # Also get text as fallback
        }

        # Build search kwargs (top-level search options)
        search_kwargs: dict = {
            "num_results": num_results,
            "contents": contents_opts,
        }

        # Add category if specified
        if category:
            search_kwargs["category"] = category

        # Add domain filtering (include_domains takes precedence)
        if include_domains:
            search_kwargs["include_domains"] = include_domains
        elif effective_excludes:
            search_kwargs["exclude_domains"] = effective_excludes

        # Log the full search configuration
        logger.debug(
            f"[Exa] Search config | "
            f"num_results={num_results} "
            f"category={category} "
            f"include_domains={include_domains} "
            f"exclude_domains={effective_excludes[:3] if effective_excludes else None}... "
            f"({len(effective_excludes) if effective_excludes else 0} total) "
            f"contents={contents_opts}"
        )

        # Use search_and_contents for better results
        logger.debug("[Exa] Calling search_and_contents API...")
        response = client.search_and_contents(query, **search_kwargs)

        # Log response metadata
        result_count = len(response.results) if response.results else 0
        logger.debug(f"[Exa] API response | results_count={result_count}")

        results: list[ExaSearchResult] = []
        for i, result in enumerate(response.results):
            # Log each result's available fields
            has_summary = hasattr(result, "summary") and bool(result.summary)
            has_text = hasattr(result, "text") and bool(result.text)
            text_len = len(result.text) if has_text else 0
            summary_len = len(result.summary) if has_summary else 0

            logger.debug(
                f"[Exa] Result {i + 1} | "
                f"title='{(result.title or 'N/A')[:50]}' "
                f"url={result.url} "
                f"has_summary={has_summary} (len={summary_len}) "
                f"has_text={has_text} (len={text_len}) "
                f"published={getattr(result, 'published_date', None)}"
            )

            # Prefer summary over raw text, fall back to text
            snippet = None
            if has_summary:
                snippet = result.summary
                logger.debug(f"[Exa] Result {i + 1} | Using summary: '{snippet[:100]}...'")
            elif has_text:
                # Clean up text: skip navigation/header cruft at the beginning
                snippet = _extract_clean_snippet(result.text, max_characters)
                logger.debug(f"[Exa] Result {i + 1} | Using text (cleaned): '{snippet[:100]}...'")
            else:
                logger.debug(f"[Exa] Result {i + 1} | No content available")

            results.append(
                ExaSearchResult(
                    title=result.title or "Untitled",
                    url=result.url,
                    snippet=snippet,
                    published_date=getattr(result, "published_date", None),
                )
            )

        logger.info(f"[Exa] Search completed | query='{query[:50]}' results={len(results)}")
        _record_exa_usage(
            model="search",
            usage={"request_count": 1, "resource_count": len(results)},
            telemetry=telemetry,
            metadata={
                "query": query[:500],
                "requested_num_results": num_results,
                "returned_num_results": len(results),
                "includes_summary": True,
                "includes_text": True,
                "category": category,
                "include_domains": include_domains,
                "exclude_domains": effective_excludes,
            },
        )
        return results

    except Exception as e:
        logger.error(f"[Exa] Search failed | query='{query[:50]}' error={e}", exc_info=True)
        if raise_on_error:
            raise ExaRequestError(f"Exa search failed for query '{query[:50]}'") from e
        return []


def exa_get_contents(
    urls: list[str],
    *,
    max_characters: int | None = 4000,
    livecrawl: Literal["always", "fallback", "never", "auto", "preferred"] | None = None,
    max_age_hours: int | None = None,
    raise_on_error: bool = False,
    telemetry: dict[str, Any] | None = None,
) -> list[ExaContentResult]:
    """Fetch content for already-selected URLs via Exa's contents API."""

    client = get_exa_client()
    if client is None:
        message = "Exa client not available"
        if raise_on_error:
            logger.error(message)
            raise ExaUnavailableError(message)
        logger.warning("%s; returning empty content results", message)
        return []

    clean_urls = [url.strip() for url in urls if isinstance(url, str) and url.strip()]
    if not clean_urls:
        return []

    try:
        logger.info("[Exa] Fetching contents for %d URLs", len(clean_urls))
        text_option: Any = True
        if max_characters is not None:
            text_option = {"max_characters": max_characters}

        response = client.get_contents(
            clean_urls,
            text=text_option,
            livecrawl=livecrawl,
            max_age_hours=max_age_hours,
        )

        results: list[ExaContentResult] = []
        for result in response.results:
            results.append(
                ExaContentResult(
                    title=result.title or "Untitled",
                    url=result.url,
                    text=getattr(result, "text", None),
                    summary=getattr(result, "summary", None),
                    published_date=getattr(result, "published_date", None),
                )
            )

        logger.info(
            "[Exa] Content fetch completed | urls=%d results=%d",
            len(clean_urls),
            len(results),
        )
        _record_exa_usage(
            model="contents",
            usage={"request_count": 1, "resource_count": len(clean_urls)},
            telemetry=telemetry,
            metadata={
                "url_count": len(clean_urls),
                "returned_num_results": len(results),
                "content_types_requested": ["text"],
                "max_characters": max_characters,
                "livecrawl": livecrawl,
                "max_age_hours": max_age_hours,
            },
        )
        return results
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[Exa] Content fetch failed | urls=%d error=%s",
            len(clean_urls),
            exc,
            exc_info=True,
        )
        if raise_on_error:
            raise ExaRequestError(f"Exa content fetch failed for {len(clean_urls)} URL(s)") from exc
        return []


def format_exa_results_for_context(results: list[ExaSearchResult]) -> str:
    """Format Exa search results as context string for LLM.

    Args:
        results: List of search results.

    Returns:
        Formatted string suitable for including in LLM context.
    """
    if not results:
        return "No web search results found."

    lines = ["Web search results:"]
    for i, result in enumerate(results, 1):
        lines.append(f"\n[{i}] {result.title}")
        lines.append(f"    URL: {result.url}")
        if result.snippet:
            lines.append(f"    {result.snippet[:300]}...")

    return "\n".join(lines)


def _record_exa_usage(
    *,
    model: str,
    usage: dict[str, int | None],
    telemetry: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    telemetry_data = telemetry or {}
    merged_metadata: dict[str, Any] = {}
    if isinstance(telemetry_data.get("metadata"), dict):
        merged_metadata.update(telemetry_data["metadata"])
    if metadata:
        merged_metadata.update(metadata)

    record_vendor_usage_out_of_band(
        provider="exa",
        model=model,
        feature=telemetry_data.get("feature") or "exa",
        operation=telemetry_data.get("operation") or f"exa.{model}",
        source=telemetry_data.get("source"),
        usage=usage,
        request_id=telemetry_data.get("request_id"),
        task_id=telemetry_data.get("task_id"),
        content_id=telemetry_data.get("content_id"),
        session_id=telemetry_data.get("session_id"),
        message_id=telemetry_data.get("message_id"),
        user_id=telemetry_data.get("user_id"),
        metadata=merged_metadata or None,
    )
