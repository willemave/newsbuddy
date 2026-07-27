"""Source-aware summarization template routing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from app.models.contracts import SummaryVersion

EDITORIAL_PROMPT_TYPES = {
    "editorial_narrative",
    "editorial_podcast",
    "editorial_substack",
    "editorial_twitter",
    "editorial_research",
    "editorial_github",
}

SPECIALIZED_EDITORIAL_PROMPT_TYPES = EDITORIAL_PROMPT_TYPES - {"editorial_narrative"}

RESEARCH_HOSTS = {
    "arxiv.org",
    "paperswithcode.com",
    "openreview.net",
    "biorxiv.org",
    "medrxiv.org",
}

GITHUB_HOSTS = {
    "github.com",
    "www.github.com",
    "gist.github.com",
    "raw.githubusercontent.com",
}

TWITTER_HOSTS = {
    "twitter.com",
    "www.twitter.com",
    "x.com",
    "www.x.com",
}


def _normalize_string(value: Any) -> str | None:
    """Return a stripped lowercase string when available."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _resolve_host(url: str | None) -> str | None:
    """Extract a normalized hostname from a URL."""
    if not isinstance(url, str) or not url.strip():
        return None
    parsed = urlparse(url.strip())
    hostname = parsed.netloc.strip().lower()
    return hostname or None


def is_editorial_prompt_type(prompt_type: str) -> bool:
    """Return True when the prompt type maps to editorial narrative output."""
    return prompt_type in EDITORIAL_PROMPT_TYPES


def resolve_editorial_summary_version(prompt_type: str) -> int:
    """Return summary version for editorial narrative outputs."""
    if prompt_type in SPECIALIZED_EDITORIAL_PROMPT_TYPES:
        return SummaryVersion.V2.value
    return SummaryVersion.V1.value


def resolve_summarization_prompt_route(
    content_type: str,
    *,
    url: str | None = None,
    platform: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[str, int, int]:
    """Resolve the summarization prompt type and limits for one content item."""
    metadata_map = metadata or {}
    normalized_content_type = _normalize_string(content_type) or ""

    if normalized_content_type == "news":
        return "news", 4, 0

    if normalized_content_type == "podcast":
        return "editorial_podcast", 10, 4

    if normalized_content_type != "article":
        return "structured", 6, 8

    normalized_platform = _normalize_string(platform) or _normalize_string(
        metadata_map.get("platform")
    )
    normalized_host = _resolve_host(url)
    metadata_content_type = _normalize_string(metadata_map.get("content_type"))
    normalized_url = (url or "").strip().lower()

    if (
        metadata_content_type == "pdf"
        or normalized_url.endswith(".pdf")
        or normalized_host in RESEARCH_HOSTS
    ):
        return "editorial_research", 10, 4

    if normalized_platform == "github" or normalized_host in GITHUB_HOSTS:
        return "editorial_github", 10, 4

    if normalized_platform == "twitter" or normalized_host in TWITTER_HOSTS:
        return "editorial_twitter", 8, 3

    if normalized_platform == "substack" or (
        normalized_host is not None and normalized_host.endswith(".substack.com")
    ):
        return "editorial_substack", 10, 4

    return "editorial_narrative", 10, 4
