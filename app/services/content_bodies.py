"""Canonical content body persistence, lookup, and metadata sanitization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from botocore.exceptions import ClientError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.schema import Content, ContentBody
from app.services.gateways.object_storage_gateway import (
    ObjectStorageGateway,
    get_object_storage_gateway,
)
from app.utils.summary_utils import extract_short_summary, extract_summary_text

LEGACY_RAW_METADATA_KEYS: tuple[str, ...] = (
    "content",
    "transcript",
    "content_to_summarize",
    "file_path",
    "transcript_path",
    "full_text",
)
API_METADATA_REDACT_KEYS: tuple[str, ...] = LEGACY_RAW_METADATA_KEYS + (
    "storage_key",
    "storage_bucket",
)
API_METADATA_INTERNAL_KEYS: tuple[str, ...] = (
    "domain",
    "processing",
)
API_METADATA_LARGE_VALUE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "summary",
        "article",
        "aggregator",
        "discussion_url",
        "source",
        "platform",
        "discovery_time",
        "publication_date",
        "top_comment",
        "comment_count",
        "detected_feed",
        "source_type",
        "source_label",
        "source_external_id",
        "author",
        "content_type",
        "workflow_from",
        "workflow_to",
        "workflow_transition",
        "summary_kind",
        "summary_version",
        "summarization_date",
    }
)
API_METADATA_MAX_VALUE_CHARS = 12_000

logger = get_logger(__name__)


class ContentBodyVariant(StrEnum):
    """Supported canonical body variants."""

    SOURCE = "source"
    RENDERED = "rendered"


class ContentBodyFormat(StrEnum):
    """Stored content formats."""

    TEXT = "text"
    MARKDOWN = "markdown"


@dataclass(frozen=True)
class ResolvedContentBody:
    """Resolved full body payload returned to API/services."""

    content_id: int
    variant: ContentBodyVariant
    kind: str
    format: ContentBodyFormat
    text: str
    updated_at: datetime | None


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _require_content_id(content: Content) -> int:
    content_id = content.id
    if content_id is None:
        raise ValueError("Content row is missing an id")
    return int(content_id)


def _require_content_type(content: Content) -> str:
    content_type = content.content_type
    if not isinstance(content_type, str) or not content_type:
        raise ValueError("Content row is missing a content_type")
    return content_type


def build_content_body_storage_key(
    *,
    content_id: int,
    variant: ContentBodyVariant,
    sha256: str,
    content_format: ContentBodyFormat,
) -> str:
    """Return the canonical object key for one content body."""
    settings = get_settings()
    extension = "md" if content_format == ContentBodyFormat.MARKDOWN else "txt"
    prefix = settings.storage.content_body_storage_prefix.strip("/")
    return f"{prefix}/{content_id}/{variant.value}-{sha256}.{extension}"


def extract_source_body_text(content_type: str, metadata: dict[str, Any]) -> str | None:
    """Return the canonical source body text using content-type-specific precedence."""
    if content_type == "podcast":
        return _clean_text(metadata.get("transcript")) or _clean_text(
            metadata.get("content_to_summarize")
        )
    if content_type in {"article", "news"}:
        return _clean_text(metadata.get("content_to_summarize")) or _clean_text(
            metadata.get("content")
        )
    return None


def extract_rendered_body_text(metadata: dict[str, Any]) -> str | None:
    """Return readable rendered markdown when present."""
    summary = metadata.get("summary")
    if not isinstance(summary, dict):
        return None
    return _clean_text(summary.get("full_markdown"))


def compute_content_excerpt(metadata: dict[str, Any], source_text: str | None) -> str | None:
    """Return a short body excerpt to keep in the DB."""
    explicit_excerpt = _clean_text(metadata.get("excerpt"))
    if explicit_excerpt:
        return explicit_excerpt[:1000]

    summary = metadata.get("summary")
    if isinstance(summary, dict):
        for candidate in (
            extract_short_summary(summary),
            extract_summary_text(summary),
            _clean_text(summary.get("hook")),
            _clean_text(summary.get("takeaway")),
        ):
            if candidate:
                return candidate[:1000]

    if source_text:
        compact = " ".join(source_text.split())
        if compact:
            return compact[:1000]
    return None


def build_search_corpus(
    *,
    content: Content,
    metadata: dict[str, Any],
    excerpt: str | None,
) -> str:
    """Build the compact materialized search corpus for one content item."""
    parts: list[str] = []
    metadata_source = _clean_text(metadata.get("source"))
    stored_source = _clean_text(content.source)
    for candidate in (metadata_source if metadata_source != stored_source else None, excerpt):
        text = _clean_text(candidate)
        if text:
            parts.append(text)

    summary = metadata.get("summary")
    if isinstance(summary, dict):
        for key in ("overview", "summary", "hook", "takeaway"):
            text = _clean_text(summary.get(key))
            if text:
                parts.append(text)
        for list_key in ("topics", "questions", "counter_arguments", "key_points"):
            raw_items = summary.get(list_key)
            if not isinstance(raw_items, list):
                continue
            for item in raw_items:
                if isinstance(item, dict):
                    text = _clean_text(item.get("text") or item.get("point") or item.get("topic"))
                else:
                    text = _clean_text(item)
                if text:
                    parts.append(text)

    discussion_url = _clean_text(metadata.get("discussion_url"))
    if discussion_url:
        parts.append(discussion_url)

    return "\n".join(parts).strip()


def sanitize_metadata_for_api(metadata: dict[str, Any]) -> dict[str, Any]:
    """Strip raw/full-body and storage internals from API metadata."""
    sanitized = dict(metadata)
    for key in API_METADATA_REDACT_KEYS:
        sanitized.pop(key, None)
    for key in API_METADATA_INTERNAL_KEYS:
        sanitized.pop(key, None)

    summary = sanitized.get("summary")
    if isinstance(summary, dict) and "full_markdown" in summary:
        summary_copy = dict(summary)
        summary_copy.pop("full_markdown", None)
        sanitized["summary"] = summary_copy

    for key in list(sanitized.keys()):
        if key in API_METADATA_LARGE_VALUE_ALLOWLIST:
            continue
        if _serialized_metadata_size(sanitized[key]) > API_METADATA_MAX_VALUE_CHARS:
            sanitized.pop(key, None)

    return sanitized


def _serialized_metadata_size(value: Any) -> int:
    """Return a conservative serialized size estimate for one metadata value."""
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except TypeError:
        return len(str(value))


def strip_legacy_body_fields(metadata: dict[str, Any]) -> dict[str, Any]:
    """Strip canonical body payloads from metadata after persistence."""
    stripped = dict(metadata)
    for key in LEGACY_RAW_METADATA_KEYS:
        stripped.pop(key, None)

    summary = stripped.get("summary")
    if isinstance(summary, dict) and "full_markdown" in summary:
        summary_copy = dict(summary)
        summary_copy.pop("full_markdown", None)
        stripped["summary"] = summary_copy
    return stripped


def persist_content_body(
    db: Session,
    *,
    content_id: int,
    variant: ContentBodyVariant,
    text: str,
    content_format: ContentBodyFormat,
    gateway: ObjectStorageGateway | None = None,
) -> ContentBody:
    """Persist one canonical body variant and upsert its DB pointer."""
    if not text.strip():
        raise ValueError("Content body text must not be empty")

    storage_gateway = gateway or get_object_storage_gateway()
    encoded = text.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    storage_key = build_content_body_storage_key(
        content_id=content_id,
        variant=variant,
        sha256=digest,
        content_format=content_format,
    )
    content_type = "text/markdown" if content_format == ContentBodyFormat.MARKDOWN else "text/plain"
    storage_gateway.put_text(key=storage_key, text=text, content_type=content_type)

    body = (
        db.query(ContentBody)
        .filter(ContentBody.content_id == content_id, ContentBody.variant == variant.value)
        .first()
    )
    if body is None:
        body = ContentBody(content_id=content_id, variant=variant.value)
        db.add(body)

    settings = get_settings()
    body.storage_provider = settings.storage.content_body_storage_provider
    body.storage_bucket = settings.content_body_storage_bucket
    body.storage_key = storage_key
    body.content_format = content_format.value
    body.sha256 = digest
    body.byte_size = len(encoded)
    body.char_count = len(text)
    body.updated_at = datetime.now(UTC).replace(tzinfo=None)
    return body


def sync_content_body_storage(
    db: Session,
    *,
    content: Content,
    gateway: ObjectStorageGateway | None = None,
) -> dict[str, Any]:
    """Persist canonical bodies, update search text, and strip raw metadata fields."""
    metadata = dict(content.content_metadata or {})
    content_id = _require_content_id(content)
    content_type = _require_content_type(content)
    source_text = extract_source_body_text(content_type, metadata)
    if source_text:
        persist_content_body(
            db,
            content_id=content_id,
            variant=ContentBodyVariant.SOURCE,
            text=source_text,
            content_format=ContentBodyFormat.TEXT,
            gateway=gateway,
        )

    rendered_text = extract_rendered_body_text(metadata)
    if rendered_text:
        persist_content_body(
            db,
            content_id=content_id,
            variant=ContentBodyVariant.RENDERED,
            text=rendered_text,
            content_format=ContentBodyFormat.MARKDOWN,
            gateway=gateway,
        )

    excerpt = compute_content_excerpt(metadata, source_text)
    if excerpt:
        metadata["excerpt"] = excerpt
    elif "excerpt" in metadata:
        metadata.pop("excerpt", None)

    if content_type == "podcast" and source_text:
        metadata["has_transcript"] = True

    content.search_text = build_search_corpus(content=content, metadata=metadata, excerpt=excerpt)
    content.content_metadata = strip_legacy_body_fields(metadata)
    return content.content_metadata


class ContentBodyResolver:
    """Resolve canonical content bodies from storage."""

    def __init__(self, gateway: ObjectStorageGateway | None = None) -> None:
        self._gateway = gateway or get_object_storage_gateway()

    def resolve(
        self,
        db: Session,
        *,
        content: Content,
        variant: ContentBodyVariant = ContentBodyVariant.SOURCE,
    ) -> ResolvedContentBody | None:
        """Return the resolved body text for one content row."""
        content_id = _require_content_id(content)
        content_type = _require_content_type(content)
        row = (
            db.query(ContentBody)
            .filter(ContentBody.content_id == content.id, ContentBody.variant == variant.value)
            .first()
        )
        fallback_body = self._build_fallback_body(content=content, variant=variant)
        if row is None:
            return fallback_body

        storage_key = getattr(row, "storage_key", None)
        if not storage_key:
            return fallback_body

        try:
            text = self._gateway.get_text(key=storage_key)
        except FileNotFoundError:
            logger.warning(
                "Canonical content body missing from local storage; falling back to metadata",
                extra={
                    "content_id": content_id,
                    "variant": variant.value,
                    "storage_key": storage_key,
                },
            )
            return fallback_body
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code") or "")
            if error_code not in {"404", "NoSuchKey", "NotFound"}:
                raise
            logger.warning(
                "Canonical content body missing from object storage; falling back to metadata",
                extra={
                    "content_id": content_id,
                    "variant": variant.value,
                    "storage_key": storage_key,
                    "error_code": error_code,
                },
            )
            return fallback_body

        content_format_value = getattr(row, "content_format", None)
        if not isinstance(content_format_value, str):
            return fallback_body

        return ResolvedContentBody(
            content_id=content_id,
            variant=variant,
            kind=_body_kind_for_content_type(content_type),
            format=ContentBodyFormat(content_format_value),
            text=text,
            updated_at=getattr(row, "updated_at", None),
        )

    def _build_fallback_body(
        self,
        *,
        content: Content,
        variant: ContentBodyVariant,
    ) -> ResolvedContentBody | None:
        metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
        content_id = _require_content_id(content)
        content_type = _require_content_type(content)
        fallback_text = (
            extract_rendered_body_text(metadata)
            if variant == ContentBodyVariant.RENDERED
            else extract_source_body_text(content_type, metadata)
        )
        if not fallback_text:
            return None

        fallback_format = (
            ContentBodyFormat.MARKDOWN
            if variant == ContentBodyVariant.RENDERED
            else ContentBodyFormat.TEXT
        )
        return ResolvedContentBody(
            content_id=content_id,
            variant=variant,
            kind=_body_kind_for_content_type(content_type),
            format=fallback_format,
            text=fallback_text,
            updated_at=getattr(content, "updated_at", None),
        )

    def resolve_text(
        self,
        db: Session,
        *,
        content: Content,
        variant: ContentBodyVariant = ContentBodyVariant.SOURCE,
    ) -> str | None:
        """Resolve text only."""
        body = self.resolve(db, content=content, variant=variant)
        if body:
            return body.text

        metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
        content_type = content.content_type
        if not isinstance(content_type, str):
            return None
        return extract_source_body_text(content_type, metadata)


def _body_kind_for_content_type(content_type: str) -> str:
    if content_type == "podcast":
        return "transcript"
    if content_type in {"article", "news"}:
        return "article"
    return "source"


_content_body_resolver: ContentBodyResolver | None = None


def get_content_body_resolver() -> ContentBodyResolver:
    """Return a cached content body resolver."""
    global _content_body_resolver
    if _content_body_resolver is None:
        _content_body_resolver = ContentBodyResolver()
    return _content_body_resolver
