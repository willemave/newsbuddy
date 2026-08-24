"""Resolve reusable host inputs for article processing."""

from __future__ import annotations

from typing import Any

from app.core.db import get_db
from app.models.contracts import ContentType
from app.models.db import Content
from app.models.domain.content import ContentData
from app.services.content_bodies import ContentBodyVariant, get_content_body_resolver
from app.utils.url_utils import is_http_url, normalize_http_url


def build_preextracted_html_data(
    content: ContentData,
    *,
    processed_url: str,
) -> dict[str, Any] | None:
    """Reuse a clean host extraction instead of launching a browser-backed strategy."""
    metadata = content.metadata or {}
    if not bool(metadata.get("analyze_url_source_body_ready")):
        return None
    with get_db() as db:
        row = db.query(Content).filter(Content.id == content.id).first()
        if row is None:
            return None
        text = get_content_body_resolver().resolve_text(
            db,
            content=row,
            variant=ContentBodyVariant.SOURCE,
        )
    if text is None:
        return None
    return {
        "title": content.title,
        "text_content": text,
        "content_type": "html",
        "source": metadata.get("source"),
        "final_url_after_redirects": processed_url,
    }


def resolve_article_processing_url(content: ContentData) -> str:
    """Select the best fetch URL for an article or news item."""
    base_url = str(content.url)
    if content.content_type != ContentType.NEWS:
        return base_url

    metadata = content.metadata or {}
    platform = (metadata.get("platform") or content.platform or "").lower()
    if is_http_url(base_url):
        return _normalize_target_url(base_url)

    article_info = metadata.get("article", {})
    candidate_urls: list[str | None] = [article_info.get("url")]
    if platform == "hackernews":
        aggregator_meta = metadata.get("aggregator", {})
        candidate_urls.append(aggregator_meta.get("metadata", {}).get("hn_linked_url"))
    candidate_urls.extend(
        [
            metadata.get("primary_article_url"),
            metadata.get("primary_url"),
            metadata.get("url"),
        ]
    )
    for candidate in candidate_urls:
        normalized = normalize_http_url(candidate) if isinstance(candidate, str) else None
        if normalized:
            return normalized
    return base_url


def _normalize_target_url(url: str) -> str:
    normalized = url.strip()
    if normalized.startswith("http://"):
        normalized = "https://" + normalized[len("http://") :]
    return normalized
