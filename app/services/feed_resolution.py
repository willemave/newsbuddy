"""Shared feed-candidate resolution helpers built on top of FeedDetector."""

from __future__ import annotations

import re
from typing import Any

from app.services.http import fetch_quiet_compat

URL_REGEX = r"https?://[^\s\"'<>]+"
URL_TRIM_CHARS = ".,);]>'\""


def extract_candidate_feed_urls(site_url: str | None, page_text: str | None) -> list[str]:
    urls: list[str] = []
    if isinstance(site_url, str) and _looks_like_feed_url(site_url):
        urls.append(site_url)

    for raw_url in _extract_urls_from_text(page_text or ""):
        if _looks_like_feed_url(raw_url):
            urls.append(raw_url)

    normalized_urls = [url.strip() for url in urls if url.strip()]
    return list(dict.fromkeys(normalized_urls))


def resolve_feed_candidate(
    *,
    detector: Any,
    title: str | None = None,
    site_url: str | None = None,
    candidate_feed_urls: list[str] | None = None,
    html_content: str | None = None,
    source: str = "feed_resolution",
    content_type: str = "article",
    prefer_site_discovery: bool = False,
) -> dict[str, str] | None:
    ordered_candidates = candidate_feed_urls or []

    if prefer_site_discovery:
        site_resolved = _resolve_from_site(
            detector=detector,
            title=title,
            site_url=site_url,
            html_content=html_content,
            source=source,
            content_type=content_type,
        )
        if site_resolved:
            return site_resolved

    for feed_url in ordered_candidates:
        validated = detector.validate_feed_url(feed_url)
        if validated:
            return validated

    if prefer_site_discovery:
        return None

    return _resolve_from_site(
        detector=detector,
        title=title,
        site_url=site_url,
        html_content=html_content,
        source=source,
        content_type=content_type,
    )


def _resolve_from_site(
    *,
    detector: Any,
    title: str | None,
    site_url: str | None,
    html_content: str | None,
    source: str,
    content_type: str,
) -> dict[str, str] | None:
    if not isinstance(site_url, str) or not site_url.strip():
        return None

    page_url = site_url.strip()
    page_html = html_content
    if page_html is None:
        try:
            response = fetch_quiet_compat(detector.http_service, page_url)
        except Exception:  # noqa: BLE001
            return None
        page_url = str(getattr(response, "url", page_url) or page_url)
        page_html = getattr(response, "text", None)

    detection = detector.detect_from_links(
        None,
        page_url=page_url,
        page_title=title,
        html_content=page_html,
        source=source,
        content_type=content_type,
        force_detect=True,
    )
    if not detection:
        return None

    detected_feed = detection.get("detected_feed") or {}
    detected_feed_url = detected_feed.get("url")
    if not isinstance(detected_feed_url, str) or not detected_feed_url.strip():
        return None

    return {
        "feed_url": detected_feed_url,
        "feed_format": detected_feed.get("format", "rss"),
        "title": detected_feed.get("title") or "",
    }


def _extract_urls_from_text(text: str) -> list[str]:
    if not text:
        return []
    return [match.rstrip(URL_TRIM_CHARS) for match in re.findall(URL_REGEX, text)]


def _looks_like_feed_url(url: str) -> bool:
    lowered = url.lower()
    return any(hint in lowered for hint in ("rss", "atom", "feed", ".xml"))
