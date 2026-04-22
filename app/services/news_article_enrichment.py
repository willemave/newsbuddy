"""Link-first article extraction for short-form news items."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.metadata import ContentType
from app.models.schema import Content, NewsItem
from app.processing_strategies.registry import StrategyRegistry, get_strategy_registry
from app.services.content_bodies import get_content_body_resolver
from app.services.gateways.object_storage_gateway import ObjectStorageGateway
from app.services.news_article_bodies import (
    NEWS_ARTICLE_BODY_REF_KEY,
    NEWS_ARTICLE_EXTRACTION_KEY,
    persist_news_item_article_body,
)
from app.services.twitter_share import extract_tweet_id
from app.services.x_tweet_metadata import build_resolved_tweet_content, hydrate_tweet_from_metadata
from app.utils.title_utils import clean_title
from app.utils.url_utils import is_http_url, normalize_http_url

logger = get_logger(__name__)

AGGREGATOR_NATIVE_DOMAINS: dict[str, set[str]] = {
    "hackernews": {"news.ycombinator.com"},
    "reddit": {"reddit.com", "www.reddit.com", "redd.it", "old.reddit.com"},
    "twitter": {"x.com", "www.x.com", "twitter.com", "www.twitter.com"},
}


@dataclass(frozen=True)
class NewsArticleEnrichmentResult:
    """Outcome of one article-body enrichment attempt."""

    success: bool
    status: str
    source: str | None = None
    article_url: str | None = None
    final_url: str | None = None
    extracted_chars: int = 0
    error_message: str | None = None


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _require_content_id(content: Content) -> int:
    """Return a persisted content ID or raise."""
    content_id = content.id
    if content_id is None:
        raise ValueError("Content must be persisted before use")
    return content_id


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _clean_title(value: Any) -> str | None:
    return clean_title(value)


def _run_strategy_method(method: Any, *args: Any, **kwargs: Any) -> Any:
    if asyncio.iscoroutinefunction(method):
        return asyncio.run(method(*args, **kwargs))
    return method(*args, **kwargs)


def _extract_host(url: str | None) -> str | None:
    normalized = normalize_http_url(url)
    if normalized is None:
        return None
    from urllib.parse import urlparse

    host = (urlparse(normalized).netloc or "").strip().lower()
    return host or None


def _existing_article_body_content(db: Session, article_url: str) -> Content | None:
    normalized = normalize_http_url(article_url)
    if normalized is None:
        return None
    return (
        db.query(Content)
        .filter(Content.content_type == ContentType.ARTICLE.value)
        .filter((Content.url == normalized) | (Content.source_url == normalized))
        .order_by(Content.id.asc())
        .first()
    )


def _choose_article_url(news_item: NewsItem) -> tuple[str | None, str | None]:
    discussion_url = normalize_http_url(news_item.discussion_url or news_item.canonical_item_url)
    for candidate in (news_item.article_url, news_item.canonical_story_url):
        normalized = normalize_http_url(candidate)
        if normalized is None or not is_http_url(normalized):
            continue
        if discussion_url and normalized == discussion_url:
            continue
        host = _extract_host(normalized)
        if (
            host
            and news_item.platform
            and host in AGGREGATOR_NATIVE_DOMAINS.get(news_item.platform, set())
        ):
            continue
        return normalized, "article_url"
    return None, None


def _update_enrichment_metadata(
    raw_metadata: dict[str, Any],
    *,
    status: str,
    source: str | None,
    article_url: str | None,
    final_url: str | None,
    strategy_name: str | None = None,
    error_message: str | None = None,
    extracted_chars: int = 0,
) -> dict[str, Any]:
    updated = dict(raw_metadata)
    updated[NEWS_ARTICLE_EXTRACTION_KEY] = {
        "status": status,
        "source": source,
        "article_url": article_url,
        "final_url": final_url,
        "strategy": strategy_name,
        "error": error_message,
        "extracted_chars": extracted_chars,
        "updated_at": _utcnow_naive().isoformat(),
    }
    return updated


def enrich_news_item_article(
    db: Session,
    *,
    news_item_id: int,
    strategy_registry: StrategyRegistry | None = None,
    gateway: ObjectStorageGateway | None = None,
) -> NewsArticleEnrichmentResult:
    """Download and persist linked article body for one news item when appropriate."""
    item = db.query(NewsItem).filter(NewsItem.id == news_item_id).first()
    if item is None:
        return NewsArticleEnrichmentResult(
            success=False,
            status="failed",
            error_message="News item not found",
        )

    raw_metadata = dict(item.raw_metadata or {})
    if isinstance(raw_metadata.get(NEWS_ARTICLE_BODY_REF_KEY), dict):
        item.raw_metadata = _update_enrichment_metadata(
            raw_metadata,
            status="completed",
            source="existing",
            article_url=normalize_http_url(item.article_url or item.canonical_story_url),
            final_url=normalize_http_url(item.article_url or item.canonical_story_url),
        )
        item.enrichment_updated_at = _utcnow_naive()
        db.commit()
        return NewsArticleEnrichmentResult(
            success=True,
            status="completed",
            source="existing",
            article_url=normalize_http_url(item.article_url or item.canonical_story_url),
            final_url=normalize_http_url(item.article_url or item.canonical_story_url),
        )

    tweet_candidate_url = normalize_http_url(item.article_url or item.canonical_story_url)
    tweet_id = extract_tweet_id(tweet_candidate_url or "")
    hydrated_tweet = (
        hydrate_tweet_from_metadata(raw_metadata, tweet_id=tweet_id) if tweet_id else None
    )
    if hydrated_tweet is not None:
        text, _, _ = build_resolved_tweet_content(hydrated_tweet.tweet)
        if text:
            raw_metadata[NEWS_ARTICLE_BODY_REF_KEY] = {
                "kind": "inline",
                "text": text,
                "source_url": tweet_candidate_url,
                "updated_at": _utcnow_naive().isoformat(),
            }
            item.raw_metadata = _update_enrichment_metadata(
                raw_metadata,
                status="completed",
                source="metadata",
                article_url=tweet_candidate_url,
                final_url=tweet_candidate_url,
                extracted_chars=len(text),
            )
            item.enrichment_updated_at = _utcnow_naive()
            db.commit()
            return NewsArticleEnrichmentResult(
                success=True,
                status="completed",
                source="metadata",
                article_url=tweet_candidate_url,
                final_url=tweet_candidate_url,
                extracted_chars=len(text),
            )

    article_url, _ = _choose_article_url(item)
    if article_url is None:
        item.raw_metadata = _update_enrichment_metadata(
            raw_metadata,
            status="skipped",
            source=None,
            article_url=None,
            final_url=None,
            error_message="No outbound article URL to enrich",
        )
        item.enrichment_updated_at = _utcnow_naive()
        db.commit()
        return NewsArticleEnrichmentResult(success=True, status="skipped")

    existing_article = _existing_article_body_content(db, article_url)
    if existing_article is not None:
        existing_text = get_content_body_resolver().resolve_text(db, content=existing_article)
        if existing_text:
            raw_metadata[NEWS_ARTICLE_BODY_REF_KEY] = {
                "kind": "content",
                "content_id": _require_content_id(existing_article),
                "variant": "source",
                "source_url": article_url,
                "updated_at": _utcnow_naive().isoformat(),
            }
            item.raw_metadata = _update_enrichment_metadata(
                raw_metadata,
                status="completed",
                source="content",
                article_url=article_url,
                final_url=normalize_http_url(existing_article.url or existing_article.source_url),
                extracted_chars=len(existing_text),
            )
            item.enrichment_updated_at = _utcnow_naive()
            db.commit()
            return NewsArticleEnrichmentResult(
                success=True,
                status="completed",
                source="content",
                article_url=article_url,
                final_url=normalize_http_url(existing_article.url or existing_article.source_url),
                extracted_chars=len(existing_text),
            )

    registry = strategy_registry or get_strategy_registry()
    strategy = registry.get_strategy(article_url)
    if strategy is None:
        item.raw_metadata = _update_enrichment_metadata(
            raw_metadata,
            status="skipped",
            source=None,
            article_url=article_url,
            final_url=article_url,
            error_message="No extraction strategy available",
        )
        item.enrichment_updated_at = _utcnow_naive()
        db.commit()
        return NewsArticleEnrichmentResult(success=True, status="skipped", article_url=article_url)

    processed_url = strategy.preprocess_url(article_url)
    strategy_name = strategy.__class__.__name__
    extraction_context = {
        "content_id": news_item_id,
        "existing_metadata": raw_metadata,
        "original_url": article_url,
    }
    try:
        extracted_data: dict[str, Any] | None = None
        while True:
            raw_content = _run_strategy_method(strategy.download_content, processed_url)
            extracted_data = (
                _run_strategy_method(
                    strategy.extract_data,
                    raw_content,
                    processed_url,
                    context=extraction_context,
                )
                or {}
            )
            delegated_url = normalize_http_url(extracted_data.get("next_url_to_process"))
            if delegated_url is None:
                break
            processed_url = delegated_url

        if extracted_data is None:
            raise ValueError("Article extraction produced no data")

        llm_data = _run_strategy_method(strategy.prepare_for_llm, extracted_data) or {}
        source_text = _clean_string(llm_data.get("content_to_summarize"))
        if source_text is None:
            raise ValueError("Article extraction did not yield content_to_summarize")

        final_url = (
            normalize_http_url(extracted_data.get("final_url_after_redirects") or processed_url)
            or article_url
        )
        raw_metadata[NEWS_ARTICLE_BODY_REF_KEY] = persist_news_item_article_body(
            db,
            news_item=item,
            text=source_text,
            source_url=article_url,
            final_url=final_url,
            gateway=gateway,
        )
        item.raw_metadata = _update_enrichment_metadata(
            raw_metadata,
            status="completed",
            source="storage",
            article_url=article_url,
            final_url=final_url,
            strategy_name=strategy_name,
            extracted_chars=len(source_text),
        )
        final_title = _clean_title(extracted_data.get("title"))
        if final_title and _clean_title(item.article_title) is None:
            item.article_title = final_title
        final_source = _clean_string(extracted_data.get("source"))
        if final_source and not _clean_string(item.article_domain):
            item.article_domain = final_source
        if final_url:
            item.article_url = final_url
            if not normalize_http_url(item.canonical_story_url):
                item.canonical_story_url = final_url
        item.enrichment_updated_at = _utcnow_naive()
        db.commit()
        return NewsArticleEnrichmentResult(
            success=True,
            status="completed",
            source="storage",
            article_url=article_url,
            final_url=final_url,
            extracted_chars=len(source_text),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "News article enrichment failed softly",
            extra={
                "component": "news_article_enrichment",
                "operation": "enrich_news_item_article",
                "item_id": str(news_item_id),
                "context_data": {
                    "article_url": processed_url,
                    "strategy": strategy_name,
                    "error": str(exc),
                },
            },
        )
        item.raw_metadata = _update_enrichment_metadata(
            raw_metadata,
            status="failed",
            source=None,
            article_url=article_url,
            final_url=normalize_http_url(processed_url),
            strategy_name=strategy_name,
            error_message=str(exc),
        )
        item.enrichment_updated_at = _utcnow_naive()
        db.commit()
        return NewsArticleEnrichmentResult(
            success=False,
            status="failed",
            article_url=article_url,
            final_url=normalize_http_url(processed_url),
            error_message=str(exc),
        )
