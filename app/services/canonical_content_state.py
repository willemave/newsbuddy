"""Relink user-owned state when content resolves to a canonical row."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.db import (
    ChatSession,
    ContentKnowledgeSave,
    ContentReadStatus,
    ContentStatusEntry,
    ContentUnlikes,
)
from app.models.metadata.access import metadata_view
from app.services.agent_data_events import enqueue_agent_data_sync
from app.services.x_bookmark_destinations import reconcile_x_bookmark_destinations_for_content

logger = get_logger(__name__)


def _relink_unique_user_content_rows(
    db: Session,
    *,
    model: type[Any],
    loser_content_id: int,
    winner_content_id: int,
    newest_timestamp_fields: tuple[str, ...] = (),
) -> set[int]:
    """Move one unique-per-user content overlay without creating duplicates."""
    loser_rows = db.query(model).filter(model.content_id == loser_content_id).all()
    if not loser_rows:
        return set()

    user_ids = {int(row.user_id) for row in loser_rows}
    winner_rows = (
        db.query(model)
        .filter(model.content_id == winner_content_id)
        .filter(model.user_id.in_(user_ids))
        .all()
    )
    winner_by_user_id = {int(row.user_id): row for row in winner_rows}

    for loser_row in loser_rows:
        user_id = int(loser_row.user_id)
        winner_row = winner_by_user_id.get(user_id)
        if winner_row is None:
            loser_row.content_id = winner_content_id
            continue

        for field_name in newest_timestamp_fields:
            loser_value = getattr(loser_row, field_name, None)
            winner_value = getattr(winner_row, field_name, None)
            if loser_value is not None and (winner_value is None or loser_value > winner_value):
                setattr(winner_row, field_name, loser_value)
        db.delete(loser_row)

    return user_ids


def finalize_canonical_user_state(
    db: Session,
    *,
    loser_content_id: int,
    winner_content_id: int,
) -> set[int]:
    """Relink user overlays and chat destinations to canonical content.

    The operation is idempotent: an existing winner overlay absorbs the loser
    row, while a non-colliding row moves in place. Chat sessions retain their
    independent histories and only change content destination.
    """
    affected_library_user_ids: set[int] = set()
    _relink_unique_user_content_rows(
        db,
        model=ContentStatusEntry,
        loser_content_id=loser_content_id,
        winner_content_id=winner_content_id,
    )
    _relink_unique_user_content_rows(
        db,
        model=ContentReadStatus,
        loser_content_id=loser_content_id,
        winner_content_id=winner_content_id,
        newest_timestamp_fields=("read_at",),
    )
    affected_library_user_ids.update(
        _relink_unique_user_content_rows(
            db,
            model=ContentKnowledgeSave,
            loser_content_id=loser_content_id,
            winner_content_id=winner_content_id,
            newest_timestamp_fields=("saved_at",),
        )
    )
    _relink_unique_user_content_rows(
        db,
        model=ContentUnlikes,
        loser_content_id=loser_content_id,
        winner_content_id=winner_content_id,
        newest_timestamp_fields=("unliked_at",),
    )

    chat_sessions = db.query(ChatSession).filter(ChatSession.content_id == loser_content_id).all()
    for session in chat_sessions:
        if session.user_id is not None:
            affected_library_user_ids.add(int(session.user_id))
        session.content_id = winner_content_id
    return affected_library_user_ids


def sync_canonical_personal_library(
    db: Session,
    *,
    user_ids: set[int],
    loser_content_id: int,
    winner_content_id: int,
) -> None:
    """Refresh agent-data projections after Knowledge or chat destinations move."""
    try:
        for user_id in sorted(user_ids):
            enqueue_agent_data_sync(
                db,
                user_id=user_id,
                content_ids=(loser_content_id, winner_content_id),
            )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception(
            "Failed to enqueue canonical agent-data projection",
            extra={
                "component": "content_worker",
                "operation": "canonical_user_state_sync",
                "context_data": {"user_ids": sorted(user_ids)},
            },
        )


def reconcile_canonical_x_bookmark_destinations(
    db: Session,
    *,
    content_id: int,
    metadata: dict[str, Any],
) -> None:
    """Move X bookmark destinations when processing chose a canonical row."""
    view = metadata_view(metadata)
    raw_canonical_id = view.processing_flag("canonical_content_id")
    try:
        canonical_id = int(raw_canonical_id)
    except (TypeError, ValueError):
        return
    if canonical_id <= 0 or canonical_id == content_id:
        return

    submitted_via = str(view.processing_flag("submitted_via") or "").strip().lower()
    reconcile_x_bookmark_destinations_for_content(
        db,
        bookmark_content_id=content_id,
        fallback_user_id=(view.submission_user_id() if submitted_via == "x_bookmarks" else None),
    )
