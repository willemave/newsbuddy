"""Helpers for transitioning metadata from flat blobs to structured state.

Migration strategy:
    Phase 1 (current): dual-write — top-level keys are preserved alongside
    ``domain``/``processing`` namespaces so legacy readers keep working.
    Phase 2: migrate all readers to ``merge_runtime_metadata()`` or direct
    namespace access, then stop writing top-level duplicates.
    Phase 3: one-off migration script to strip top-level duplicates from DB.
"""

from __future__ import annotations

from typing import Any

DOMAIN_KEY = "domain"
PROCESSING_KEY = "processing"

# Runtime/operational keys that should live under `processing`.
PROCESSING_FIELD_NAMES: set[str] = {
    "subscribe_to_feed",
    "feed_subscription",
    "detected_feed",
    "all_detected_feeds",
    "share_and_chat_user_ids",
    "share_and_chat_requests",
    "submitted_by_user_id",
    "submitted_via",
    "platform_hint",
    "content_to_summarize",
    "processing_errors",
    "canonical_content_id",
    "tweet_enrichment",
    "tweet_only",
}


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def normalize_metadata_shape(raw_metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Return metadata with explicit `domain` and `processing` namespaces.

    This helper is backward-compatible: existing top-level fields remain untouched,
    while nested `domain`/`processing` mirrors are materialized for new code paths.

    Args:
        raw_metadata: Existing metadata payload.

    Returns:
        Normalized metadata dictionary with both namespaces present.
    """
    metadata = dict(raw_metadata or {})

    domain = metadata.get(DOMAIN_KEY)
    if not isinstance(domain, dict):
        domain = {}

    processing = metadata.get(PROCESSING_KEY)
    if not isinstance(processing, dict):
        processing = {}

    for key, value in metadata.items():
        if key in {DOMAIN_KEY, PROCESSING_KEY}:
            continue
        if key in PROCESSING_FIELD_NAMES:
            processing.setdefault(key, value)
        else:
            domain.setdefault(key, value)

    metadata[DOMAIN_KEY] = domain
    metadata[PROCESSING_KEY] = processing
    return metadata


def merge_runtime_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Return a flat compatibility view of metadata.

    Args:
        metadata: Structured or legacy metadata payload.

    Returns:
        Flat metadata with `domain` values overlaid by `processing` values.
    """
    normalized = normalize_metadata_shape(metadata)
    merged: dict[str, Any] = {}
    merged.update(normalized.get(DOMAIN_KEY, {}))
    merged.update(normalized.get(PROCESSING_KEY, {}))
    return merged


def update_processing_state(
    metadata: dict[str, Any] | None,
    **processing_fields: Any,
) -> dict[str, Any]:
    """Set processing fields in metadata while preserving compatibility.

    Args:
        metadata: Existing metadata.
        **processing_fields: Processing fields to set.

    Returns:
        Updated metadata containing the new processing values both in
        `processing` namespace and at top-level for legacy readers.
    """
    normalized = normalize_metadata_shape(metadata)
    processing = dict(normalized.get(PROCESSING_KEY, {}))
    processing.update(processing_fields)
    normalized[PROCESSING_KEY] = processing

    for key, value in processing_fields.items():
        normalized[key] = value
    return normalized


def remove_processing_fields(
    metadata: dict[str, Any] | None,
    *field_names: str,
) -> dict[str, Any]:
    """Remove processing fields from both structured and flat metadata."""
    normalized = normalize_metadata_shape(metadata)
    processing = dict(normalized.get(PROCESSING_KEY, {}))
    for field_name in field_names:
        normalized.pop(field_name, None)
        processing.pop(field_name, None)
    normalized[PROCESSING_KEY] = processing
    return normalized


def extract_share_and_chat_user_ids(metadata: dict[str, Any] | None) -> list[int]:
    """Return valid pending share-and-chat user IDs from metadata."""
    raw_users = merge_runtime_metadata(metadata).get("share_and_chat_user_ids")
    raw_values = raw_users if isinstance(raw_users, list) else [raw_users]
    user_ids: list[int] = []

    for raw_value in raw_values:
        user_id = _coerce_positive_int(raw_value)
        if user_id is not None and user_id not in user_ids:
            user_ids.append(user_id)

    return user_ids


def extract_share_and_chat_requests(
    metadata: dict[str, Any] | None,
) -> list[dict[str, object]]:
    """Return pending share-and-chat requests, including legacy user-id entries."""
    metadata_view = merge_runtime_metadata(metadata)
    requests: list[dict[str, object]] = []
    raw_requests = metadata_view.get("share_and_chat_requests")

    if isinstance(raw_requests, list):
        for raw_request in raw_requests:
            if not isinstance(raw_request, dict):
                continue
            user_id = _coerce_positive_int(raw_request.get("user_id"))
            if user_id is None:
                continue
            request: dict[str, object] = {"user_id": user_id}
            initial_message = _clean_text(raw_request.get("initial_message"))
            if initial_message:
                request["initial_message"] = initial_message
            requests.append(request)

    existing_user_ids: set[int] = set()
    for request in requests:
        request_user_id = request.get("user_id")
        if isinstance(request_user_id, int):
            existing_user_ids.add(request_user_id)
    for user_id in extract_share_and_chat_user_ids(metadata_view):
        if user_id not in existing_user_ids:
            requests.append({"user_id": user_id})
            existing_user_ids.add(user_id)

    return requests


def append_share_and_chat_request(
    metadata: dict[str, Any] | None,
    *,
    user_id: int,
    initial_message: str | None,
) -> dict[str, Any]:
    """Record or replace one pending share-and-chat request."""
    normalized = normalize_metadata_shape(metadata)
    request_user_ids = extract_share_and_chat_user_ids(normalized)
    if user_id not in request_user_ids:
        request_user_ids.append(user_id)

    request: dict[str, object] = {"user_id": user_id}
    cleaned_message = _clean_text(initial_message)
    if cleaned_message:
        request["initial_message"] = cleaned_message

    requests = [
        existing
        for existing in extract_share_and_chat_requests(normalized)
        if existing.get("user_id") != user_id
    ]
    requests.append(request)
    return update_processing_state(
        normalized,
        share_and_chat_user_ids=request_user_ids,
        share_and_chat_requests=requests,
    )
