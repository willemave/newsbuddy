"""Converters between domain models and database models."""

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.models.metadata import ContentData, ContentStatus, ContentType
from app.models.metadata_state import merge_runtime_metadata, normalize_metadata_shape
from app.models.schema import Content as DBContent
from app.utils.summary_metadata import infer_summary_kind_version
from app.utils.url_utils import is_http_url

logger = get_logger(__name__)


def _is_user_scoped_x_digest_url(raw_url: str, metadata: dict[str, Any], content_type: str) -> bool:
    """Return True when the stored URL is an internal per-user X digest key."""
    if content_type != ContentType.NEWS.value:
        return False
    if "#newsly-digest-user-" not in raw_url:
        return False

    source_type = str(metadata.get("source_type") or "").strip().lower()
    return source_type in {"x_timeline", "x_list"} or bool(metadata.get("tweet_id"))


def _select_http_url(raw_url: str, metadata: dict[str, Any], content_type: str) -> str:
    if _is_user_scoped_x_digest_url(raw_url, metadata, content_type):
        return raw_url

    candidates: list[str | None] = [raw_url]

    if content_type == ContentType.NEWS.value:
        article = metadata.get("article")
        if isinstance(article, dict):
            candidates.insert(0, article.get("url"))

    candidates.extend(
        [
            metadata.get("final_url_after_redirects"),
            metadata.get("final_url"),
            metadata.get("url"),
        ]
    )

    for candidate in candidates:
        if isinstance(candidate, str) and is_http_url(candidate):
            return candidate

    return raw_url


def content_to_domain(db_content: DBContent) -> ContentData:
    """Convert database Content to domain ContentData."""
    try:
        stored_metadata = normalize_metadata_shape(dict(db_content.content_metadata or {}))
        metadata = merge_runtime_metadata(stored_metadata)

        if db_content.platform and metadata.get("platform") is None:
            metadata["platform"] = db_content.platform
        if db_content.source and metadata.get("source") is None:
            metadata["source"] = db_content.source
        _normalize_summary_metadata(metadata, db_content.content_type)

        resolved_url = _select_http_url(
            db_content.url,
            metadata,
            db_content.content_type,
        )

        return ContentData(
            id=db_content.id,
            content_type=ContentType(db_content.content_type),
            url=resolved_url,
            source_url=db_content.source_url or db_content.url,
            title=db_content.title,
            status=ContentStatus(db_content.status),
            metadata=metadata,
            platform=db_content.platform,
            source=db_content.source,
            error_message=db_content.error_message,
            retry_count=db_content.retry_count or 0,
            created_at=db_content.created_at,
            processed_at=db_content.processed_at,
            publication_date=db_content.publication_date,
        )
    except Exception as e:
        logger.exception(
            "Error converting content %s: %s",
            db_content.id,
            e,
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
    summary = metadata.get("summary")
    if not isinstance(summary, dict):
        return

    summary_kind = metadata.get("summary_kind")
    summary_version = metadata.get("summary_version")
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
    """Convert domain ContentData to database Content."""
    if existing:
        # Update existing
        existing.title = content_data.title
        existing.status = content_data.status.value
        new_url = str(content_data.url)
        if new_url and new_url != existing.url:
            existing.url = new_url
        if content_data.source_url:
            existing.source_url = content_data.source_url
        elif existing.source_url is None:
            existing.source_url = new_url
        # Serialize metadata to ensure datetime objects are handled
        dumped_data = content_data.model_dump(mode="json")
        md = normalize_metadata_shape(dumped_data["metadata"] or {})
        runtime_md = merge_runtime_metadata(md)
        # Keep DB columns for platform/source in sync with metadata if provided
        plat = runtime_md.get("platform")
        src = runtime_md.get("source")
        if isinstance(plat, str) and plat.strip():
            existing.platform = plat.strip().lower()
        if isinstance(src, str) and src.strip():
            existing.source = src.strip()
        existing.content_metadata = md

        # Sync classification from summary metadata to DB column for filtering
        summary = runtime_md.get("summary")
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
    else:
        # Create new
        dumped = content_data.model_dump(mode="json")
        md = normalize_metadata_shape(dumped.get("metadata") or {})
        runtime_md = merge_runtime_metadata(md)
        plat = runtime_md.get("platform")
        src = runtime_md.get("source")
        return DBContent(
            content_type=content_data.content_type.value,
            url=str(content_data.url),
            source_url=content_data.source_url or str(content_data.url),
            title=content_data.title,
            status=content_data.status.value,
            platform=(plat.strip().lower() if isinstance(plat, str) and plat.strip() else None),
            source=(src.strip() if isinstance(src, str) and src.strip() else None),
            content_metadata=md,
            error_message=content_data.error_message,
            retry_count=content_data.retry_count,
            created_at=content_data.created_at or datetime.now(UTC),
            processed_at=content_data.processed_at,
        )
