"""Helpers for safe content metadata writes under concurrent task updates."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.schema import Content

logger = get_logger(__name__)


class ContentMetadataMergeError(RuntimeError):
    """Raised when content metadata cannot be refreshed safely."""


def compute_metadata_patch(
    base_metadata: Mapping[str, Any] | None,
    updated_metadata: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], set[str]]:
    """Compute changed and removed keys between metadata snapshots.

    Args:
        base_metadata: Metadata snapshot taken before local mutations.
        updated_metadata: Metadata after local mutations.

    Returns:
        Tuple of:
            - updates: keys whose values changed or were newly added.
            - removed_keys: keys removed by local mutations.
    """
    base = _coerce_metadata(base_metadata)
    updated = _coerce_metadata(updated_metadata)

    updates = {
        key: value
        for key, value in updated.items()
        if key not in base or base.get(key) != value
    }
    removed_keys = {key for key in base if key not in updated}
    return updates, removed_keys


def refresh_merge_content_metadata(
    db: Session,
    content_id: int | None,
    *,
    base_metadata: Mapping[str, Any] | None,
    updated_metadata: Mapping[str, Any] | None,
    latest_metadata: Mapping[str, Any] | None = None,
    preserve_latest_keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Merge metadata changes into the latest persisted metadata snapshot.

    This applies a patch (diff between ``base_metadata`` and ``updated_metadata``)
    on top of the latest metadata from the database, reducing accidental
    overwrite of unrelated concurrent updates.

    Args:
        db: Active SQLAlchemy session.
        content_id: Content identifier to refresh from DB.
        base_metadata: Metadata snapshot before local mutations.
        updated_metadata: Metadata after local mutations.
        latest_metadata: Optional already-loaded latest metadata snapshot.
        preserve_latest_keys: Keys that should always keep the latest DB values.

    Returns:
        Merged metadata dictionary ready to persist.
    """
    latest_metadata_resolved = (
        _coerce_metadata(latest_metadata)
        if latest_metadata is not None
        else _load_latest_content_metadata(db, content_id, fallback=updated_metadata)
    )
    updates, removed_keys = compute_metadata_patch(base_metadata, updated_metadata)

    merged = dict(latest_metadata_resolved)
    for key in removed_keys:
        merged.pop(key, None)
    merged.update(updates)

    if preserve_latest_keys:
        for key in preserve_latest_keys:
            if key in latest_metadata_resolved:
                merged[key] = latest_metadata_resolved[key]
            else:
                merged.pop(key, None)

    return merged


def _load_latest_content_metadata(
    db: Session,
    content_id: int | None,
    *,
    fallback: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return latest content metadata from DB."""
    if not content_id:
        return _coerce_metadata(fallback)

    row = (
        db.query(Content.content_metadata)
        .filter(Content.id == int(content_id))
        .first()
    )
    if row is None:
        logger.error("Content metadata row missing for content %s", content_id)
        raise ContentMetadataMergeError(f"Missing content metadata row for content {content_id}")
    if isinstance(row, (tuple, list)):
        if not row:
            logger.error("Content metadata row payload empty for content %s", content_id)
            raise ContentMetadataMergeError(f"Empty content metadata row for content {content_id}")
        return _coerce_persisted_metadata(row[0], content_id=content_id)
    if hasattr(row, "content_metadata"):
        return _coerce_persisted_metadata(row.content_metadata, content_id=content_id)
    return _coerce_persisted_metadata(row, content_id=content_id)


def _coerce_persisted_metadata(raw_metadata: Any, *, content_id: int) -> dict[str, Any]:
    """Return persisted metadata or raise when the DB payload is invalid."""
    if raw_metadata is None:
        return {}
    if isinstance(raw_metadata, dict):
        return dict(raw_metadata)
    logger.error(
        "Unexpected content metadata payload type for content %s: %s",
        content_id,
        type(raw_metadata).__name__,
    )
    raise ContentMetadataMergeError(
        f"Invalid content metadata payload for content {content_id}: "
        f"{type(raw_metadata).__name__}"
    )


def _coerce_metadata(raw_metadata: Mapping[str, Any] | None | Any) -> dict[str, Any]:
    """Return a plain dictionary for metadata payloads."""
    if isinstance(raw_metadata, dict):
        return dict(raw_metadata)
    return {}
