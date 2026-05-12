"""Relevant-link projection for short-form news detail."""

from __future__ import annotations

from typing import Any

from app.models.metadata.summaries import InterestingExternalLink
from app.services.interesting_external_links import select_interesting_external_links
from app.utils.url_utils import normalize_http_url

NEWS_ARTICLE_RELEVANT_LINKS_KEY = "article_relevant_links"
NEWS_RELEVANT_LINKS_KEY = "relevant_links"
MAX_NEWS_RELEVANT_LINKS = 6


def select_news_article_relevant_links(
    content_text: str | None,
    *,
    source_url: str | None,
    title: str | None = None,
    usage_persist: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Select high-signal links from a news item's linked article body."""
    links = select_interesting_external_links(
        content_text,
        source_url=source_url,
        title=title,
        usage_persist=usage_persist,
    )
    return [
        _article_link_payload(link) for link in links if normalize_http_url(link.url) is not None
    ]


def build_news_relevant_links(
    raw_metadata: dict[str, Any],
    *,
    article_url: str | None,
    discussion_url: str | None,
    discussion_summary: dict[str, Any] | None = None,
    limit: int = MAX_NEWS_RELEVANT_LINKS,
) -> list[dict[str, Any]]:
    """Merge article-derived and discussion-derived links for news detail."""
    excluded_urls = {
        normalized
        for normalized in (
            normalize_http_url(article_url),
            normalize_http_url(discussion_url),
        )
        if normalized
    }
    seen: set[str] = set()
    links: list[dict[str, Any]] = []

    def add(raw_link: dict[str, Any], *, source: str, fallback_reason: str) -> None:
        if len(links) >= limit:
            return
        normalized_url = normalize_http_url(_clean_string(raw_link.get("url")))
        if normalized_url is None or normalized_url in excluded_urls or normalized_url in seen:
            return
        seen.add(normalized_url)
        title = _clean_string(raw_link.get("title"))
        reason = _clean_string(raw_link.get("reason")) or fallback_reason
        links.append(
            {
                "url": normalized_url,
                "title": title,
                "reason": reason,
                "source": source,
            }
        )

    article_links = raw_metadata.get(NEWS_ARTICLE_RELEVANT_LINKS_KEY)
    if isinstance(article_links, list):
        for raw_link in article_links:
            if isinstance(raw_link, dict):
                add(
                    raw_link,
                    source="article",
                    fallback_reason="Useful supporting context from the article.",
                )

    if isinstance(discussion_summary, dict):
        discussion_links = discussion_summary.get("notable_links")
        if isinstance(discussion_links, list):
            for raw_link in discussion_links:
                if isinstance(raw_link, dict):
                    add(
                        raw_link,
                        source="community",
                        fallback_reason="Mentioned in the discussion.",
                    )

    return links


def _article_link_payload(link: InterestingExternalLink) -> dict[str, Any]:
    payload = link.model_dump(mode="json", exclude_none=True)
    payload["source"] = "article"
    return payload


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None
