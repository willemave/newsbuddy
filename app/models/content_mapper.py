"""Map between ORM content rows and normalized content models."""

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import HttpUrl, TypeAdapter

from app.core.logging import get_logger
from app.models.metadata import ContentData, ContentStatus, ContentType
from app.models.metadata_access import metadata_view
from app.models.metadata_state import merge_runtime_metadata, normalize_metadata_shape
from app.models.schema import Content as DBContent
from app.utils.summary_metadata import infer_summary_kind_version
from app.utils.url_utils import is_http_url

logger = get_logger(__name__)
_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


def _is_user_scoped_x_digest_url(raw_url: str, metadata: dict[str, Any], content_type: str) -> bool:
    """Return True when the stored URL is an internal per-user X digest key."""
    if content_type != ContentType.NEWS.value:
        return False
    if "#newsly-digest-user-" not in raw_url:
        return False

    view = metadata_view(metadata)
    source_type = str(view.get("source_type") or "").strip().lower()
    return source_type in {"x_timeline", "x_list"} or bool(view.get("tweet_id"))


def _select_http_url(raw_url: str, metadata: dict[str, Any], content_type: str) -> str:
    if _is_user_scoped_x_digest_url(raw_url, metadata, content_type):
        return raw_url

    candidates: list[str | None] = [raw_url]

    if content_type == ContentType.NEWS.value:
        article = metadata_view(metadata).news_fields().article
        if isinstance(article, dict):
            candidates.insert(0, article.get("url"))

    view = metadata_view(metadata)
    candidates.extend(
        [
            view.get("final_url_after_redirects"),
            view.get("final_url"),
            view.get("url"),
        ]
    )

    for candidate in candidates:
        if isinstance(candidate, str) and is_http_url(candidate):
            return candidate

    return raw_url


def _select_news_article_url(
    raw_url: str | None,
    source_url: str | None,
    metadata: dict[str, Any],
) -> str | None:
    view = metadata_view(metadata)
    candidates: list[str | None] = [
        source_url,
        view.get("final_url_after_redirects"),
        view.get("final_url"),
        view.get("url"),
        raw_url,
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and is_http_url(candidate):
            return candidate
    return None


def _backfill_news_article_metadata(db_content: DBContent, metadata: dict[str, Any]) -> None:
    if db_content.content_type != ContentType.NEWS.value:
        return

    view = metadata_view(metadata)
    article = view.news_fields().article
    if isinstance(article, dict) and isinstance(article.get("url"), str):
        return

    article_url = _select_news_article_url(db_content.url, db_content.source_url, metadata)
    if article_url is None:
        return

    parsed = urlparse(article_url)
    source = view.get("source")
    source_domain = source if isinstance(source, str) else None
    if not source_domain:
        source_domain = parsed.netloc or None

    article_metadata = article if isinstance(article, dict) else {}
    article_metadata.setdefault("url", article_url)
    article_metadata.setdefault("title", db_content.title)
    article_metadata.setdefault("source_domain", source_domain)
    metadata["article"] = article_metadata


def content_to_domain(db_content: DBContent) -> ContentData:
    """Convert database Content to normalized ContentData."""
    try:
        content_type = db_content.content_type
        url = db_content.url
        status = db_content.status
        if (
            not isinstance(content_type, str)
            or not isinstance(url, str)
            or not isinstance(status, str)
        ):
            raise ValueError(f"Content {db_content.id} is missing required string fields")

        stored_metadata = normalize_metadata_shape(dict(db_content.content_metadata or {}))
        metadata = merge_runtime_metadata(stored_metadata)

        if db_content.platform and metadata.get("platform") is None:
            metadata["platform"] = db_content.platform
        if db_content.source and metadata.get("source") is None:
            metadata["source"] = db_content.source
        _backfill_news_article_metadata(db_content, metadata)
        _normalize_summary_metadata(metadata, content_type)

        resolved_url = _select_http_url(
            url,
            metadata,
            content_type,
        )

        return ContentData(
            id=db_content.id,
            content_type=ContentType(content_type),
            url=_HTTP_URL_ADAPTER.validate_python(resolved_url),
            source_url=db_content.source_url or url,
            title=db_content.title,
            status=ContentStatus(status),
            metadata=metadata,
            platform=db_content.platform,
            source=db_content.source,
            error_message=db_content.error_message,
            retry_count=db_content.retry_count or 0,
            created_at=db_content.created_at,
            processed_at=db_content.processed_at,
            publication_date=db_content.publication_date,
        )
    except Exception as exc:
        logger.exception(
            "Error converting content %s: %s",
            db_content.id,
            exc,
            extra={
                "component": "content_converter",
                "operation": "content_to_domain",
                "context_data": {
                    "content_id": db_content.id,
                    "metadata": db_content.content_metadata,
                },
            },
        )
        raise


def _normalize_summary_metadata(metadata: dict[str, Any], content_type: str) -> None:
    view = metadata_view(metadata)
    summary = view.summary()
    if not isinstance(summary, dict):
        return

    summary_kind = view.summary_kind()
    summary_version = view.summary_version()
    if summary_kind and summary_version:
        return

    inferred = infer_summary_kind_version(content_type, summary, summary_kind, summary_version)
    if not inferred:
        return
    inferred_kind, inferred_version = inferred
    if not summary_kind:
        metadata["summary_kind"] = inferred_kind
    if not summary_version:
        metadata["summary_version"] = inferred_version


def domain_to_content(content_data: ContentData, existing: DBContent | None = None) -> DBContent:
    """Convert normalized ContentData to database Content."""
    if existing:
        existing.title = content_data.title
        existing.status = content_data.status.value
        new_url = str(content_data.url)
        if new_url and new_url != existing.url:
            existing.url = new_url
        if content_data.source_url:
            existing.source_url = content_data.source_url
        elif existing.source_url is None:
            existing.source_url = new_url

        dumped_data = content_data.model_dump(mode="json")
        metadata = normalize_metadata_shape(dumped_data["metadata"] or {})
        runtime_metadata = merge_runtime_metadata(metadata)
        platform = runtime_metadata.get("platform")
        source = runtime_metadata.get("source")
        if isinstance(platform, str) and platform.strip():
            existing.platform = platform.strip().lower()
        if isinstance(source, str) and source.strip():
            existing.source = source.strip()
        existing.content_metadata = metadata

        summary = runtime_metadata.get("summary")
        if isinstance(summary, dict):
            classification = summary.get("classification")
            if classification in ("to_read", "skip"):
                existing.classification = classification

        existing.error_message = content_data.error_message
        existing.retry_count = content_data.retry_count
        if content_data.processed_at:
            existing.processed_at = content_data.processed_at
        existing.updated_at = datetime.now(UTC)
        return existing

    dumped = content_data.model_dump(mode="json")
    metadata = normalize_metadata_shape(dumped.get("metadata") or {})
    runtime_metadata = merge_runtime_metadata(metadata)
    platform = runtime_metadata.get("platform")
    source = runtime_metadata.get("source")
    return DBContent(
        content_type=content_data.content_type.value,
        url=str(content_data.url),
        source_url=content_data.source_url or str(content_data.url),
        title=content_data.title,
        status=content_data.status.value,
        platform=(
            platform.strip().lower() if isinstance(platform, str) and platform.strip() else None
        ),
        source=(source.strip() if isinstance(source, str) and source.strip() else None),
        content_metadata=metadata,
        error_message=content_data.error_message,
        retry_count=content_data.retry_count,
        created_at=content_data.created_at or datetime.now(UTC),
        processed_at=content_data.processed_at,
    )
