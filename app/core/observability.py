"""Helpers for logger-only structured observability."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.core.logging import (
    bind_log_context,
    reset_log_context,
)

_MULTIPART_FIELD_PATTERN = re.compile(r'name="([^"]+)"')
_SAFE_HEADER_ALLOWLIST = {
    "content-type",
    "content-length",
    "user-agent",
    "x-request-id",
    "x-forwarded-for",
    "x-real-ip",
    "accept",
}
TASK_EVENT_NAMES = {
    "analyze_url": "content.analyze_url",
    "process_content": "content.process_content",
    "process_podcast_media": "content.process_podcast_media",
    "download_audio": "content.download_audio",
    "transcribe": "content.transcribe",
    "summarize": "content.summarize",
    "generate_image": "content.generate_image",
    "discover_feeds": "content.discover_feeds",
    "onboarding_discover": "content.onboarding_discover",
    "dig_deeper": "assistant.turn",
    "sync_integration": "integration.sync",
    "scrape": "scraper.run",
}


@contextmanager
def bound_log_context(**context: Any) -> Iterator[None]:
    """Bind structured logging context for a block."""
    token = bind_log_context(**context)
    try:
        yield
    finally:
        reset_log_context(token)


def build_log_extra(
    *,
    component: str,
    operation: str,
    event_name: str | None = None,
    status: str | None = None,
    duration_ms: float | int | None = None,
    item_id: str | int | None = None,
    context_data: dict[str, Any] | None = None,
    http_details: dict[str, Any] | None = None,
    **metadata: Any,
) -> dict[str, Any]:
    """Build a consistent `extra` payload for structured logger calls."""
    extra: dict[str, Any] = {
        "component": component,
        "operation": operation,
    }
    if event_name is not None:
        extra["event_name"] = event_name
    if status is not None:
        extra["status"] = status
    if duration_ms is not None:
        extra["duration_ms"] = round(float(duration_ms), 2)
    if item_id is not None:
        extra["item_id"] = item_id
    if context_data is not None:
        extra["context_data"] = context_data
    if http_details is not None:
        extra["http_details"] = http_details
    extra.update({key: value for key, value in metadata.items() if value is not None})
    return extra


def get_task_event_name(task_type: str | None) -> str:
    """Map a queue task type to a canonical lifecycle event name."""
    normalized = (task_type or "").strip().lower()
    return TASK_EVENT_NAMES.get(normalized, f"task.{normalized or 'unknown'}")


def sanitize_url_for_logs(url: str | None) -> dict[str, Any] | None:
    """Return a safe URL summary without raw query values."""
    if not isinstance(url, str) or not url.strip():
        return None

    parsed = urlsplit(url)
    query_keys = sorted(parse_qs(parsed.query, keep_blank_values=True).keys())
    sanitized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme else url.strip()
    if query_keys:
        sanitized = f"{sanitized}?keys={','.join(query_keys)}"

    return {
        "scheme": parsed.scheme or None,
        "host": parsed.netloc or None,
        "path": parsed.path or None,
        "query_keys": query_keys,
        "sanitized": sanitized,
    }


def summarize_headers(headers: Mapping[str, str] | None) -> dict[str, Any]:
    """Return a redacted request header summary."""
    if not headers:
        return {}
    summary: dict[str, Any] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered not in _SAFE_HEADER_ALLOWLIST:
            continue
        summary[key] = value
    return summary


def summarize_request_payload(
    body: bytes | None,
    content_type: str | None,
    *,
    max_json_keys: int = 20,
) -> dict[str, Any]:
    """Return a bounded request payload summary safe for logs."""
    payload_summary: dict[str, Any] = {
        "body_bytes": len(body or b""),
        "content_type": content_type or None,
    }
    if not body:
        payload_summary["shape"] = "empty"
        return payload_summary

    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_type == "application/json":
        try:
            decoded = json.loads(body.decode("utf-8", errors="replace"))
        except Exception:
            payload_summary["shape"] = "json_invalid"
            return payload_summary
        payload_summary.update(_summarize_json_payload(decoded, max_keys=max_json_keys))
        return payload_summary

    if normalized_type == "application/x-www-form-urlencoded":
        parsed = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        payload_summary["shape"] = "form"
        payload_summary["field_names"] = sorted(parsed.keys())[:max_json_keys]
        payload_summary["field_count"] = len(parsed)
        return payload_summary

    if normalized_type.startswith("multipart/form-data"):
        field_names = sorted(
            set(_MULTIPART_FIELD_PATTERN.findall(body.decode("utf-8", errors="ignore")))
        )
        payload_summary["shape"] = "multipart"
        payload_summary["field_names"] = field_names[:max_json_keys]
        payload_summary["field_count"] = len(field_names)
        return payload_summary

    payload_summary["shape"] = "binary_or_text"
    return payload_summary


def _summarize_json_payload(value: Any, *, max_keys: int) -> dict[str, Any]:
    if isinstance(value, dict):
        keys = sorted(value.keys())
        return {
            "shape": "json_object",
            "top_level_keys": keys[:max_keys],
            "top_level_key_count": len(keys),
            "large_text_present": any(_has_large_text(v) for v in value.values()),
        }
    if isinstance(value, list):
        return {
            "shape": "json_array",
            "top_level_item_count": len(value),
            "item_types": sorted({type(item).__name__ for item in value[:10]}),
            "large_text_present": any(_has_large_text(item) for item in value[:10]),
        }
    return {
        "shape": f"json_scalar:{type(value).__name__}",
        "large_text_present": _has_large_text(value),
    }


def _has_large_text(value: Any, *, threshold: int = 250) -> bool:
    if isinstance(value, str):
        return len(value) > threshold
    if isinstance(value, dict):
        return any(_has_large_text(item, threshold=threshold) for item in value.values())
    if isinstance(value, list):
        return any(_has_large_text(item, threshold=threshold) for item in value)
    return False
