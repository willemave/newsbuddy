from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import Settings
from app.models.db import BriefingPendingSource, BriefingSegment, BriefingState
from app.services.briefing import compaction
from app.services.briefing.segments import build_briefing_segment
from app.services.briefing.sources import (
    BriefingSource,
    eligible_unread_source_keys_for,
)
from app.services.briefing.state import lock_briefing_state
from app.services.briefing.window_composition import ComposedWindow

logger = get_logger(__name__)


@dataclass(frozen=True)
class AppendWindow:
    lens_id: int
    lens_key: str
    lens_title: str
    tier: str
    window_index: int
    pending_row_ids: tuple[int, ...]
    sources: tuple[BriefingSource, ...]
    event_groups: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class PublicationPlan:
    starting_version: int
    append_windows: tuple[AppendWindow, ...]
    compaction_plans: tuple[compaction.CompactionPlan, ...]


@dataclass(frozen=True)
class PublicationResult:
    state: BriefingState
    appended_segments: int
    compacted_segments: int
    stale: bool
    append_stale: bool


def publish_composed_plan(
    db: Session,
    *,
    user_id: int,
    plan: PublicationPlan,
    composed_append_windows: list[ComposedWindow[AppendWindow]],
    composed_compaction_windows: list[ComposedWindow[compaction.CompactionWindow]],
    replace_existing: bool,
    settings: Settings,
) -> PublicationResult:
    """Publish one frozen refresh plan while holding the user's state lock."""

    state = lock_briefing_state(db, user_id=user_id, settings=settings)
    current_version = int(state.version or 0)
    if current_version != plan.starting_version:
        logger.info(
            "Briefing publication plan version became stale",
            extra={
                "component": "briefing",
                "operation": "publish_refresh",
                "item_id": user_id,
                "context_data": {
                    "starting_version": plan.starting_version,
                    "current_version": current_version,
                },
            },
        )
        return PublicationResult(state, 0, 0, True, bool(plan.append_windows))

    append_current = _append_plan_is_current(
        db,
        user_id=user_id,
        planned_windows=plan.append_windows,
        composed_windows=composed_append_windows,
    )
    if replace_existing and not append_current:
        logger.info(
            "Full Briefing publication aborted because its append plan became stale",
            extra={
                "component": "briefing",
                "operation": "publish_refresh",
                "item_id": user_id,
            },
        )
        return PublicationResult(state, 0, 0, False, True)

    if replace_existing:
        db.query(BriefingSegment).filter(BriefingSegment.user_id == user_id).filter(
            BriefingSegment.status.in_(("active", "degraded"))
        ).update({BriefingSegment.status: "compacted"}, synchronize_session=False)

    appended = (
        _persist_append_windows(
            db,
            user_id=user_id,
            composed_windows=composed_append_windows,
        )
        if append_current
        else 0
    )
    compacted = compaction.persist_compactions(
        db,
        user_id=user_id,
        plans=list(plan.compaction_plans),
        composed_windows=composed_compaction_windows,
    )
    return PublicationResult(
        state=state,
        appended_segments=appended,
        compacted_segments=compacted,
        stale=False,
        append_stale=not append_current,
    )


def _append_plan_is_current(
    db: Session,
    *,
    user_id: int,
    planned_windows: tuple[AppendWindow, ...],
    composed_windows: list[ComposedWindow[AppendWindow]],
) -> bool:
    planned_shapes = [_append_window_shape(window) for window in planned_windows]
    composed_shapes = [_append_window_shape(window.prepared) for window in composed_windows]
    if composed_shapes != planned_shapes:
        _log_stale_append(user_id=user_id, reason="composition_coverage_changed")
        return False

    expected_rows: dict[int, tuple[str, int, str]] = {}
    planned_source_keys: list[str] = []
    planned_source_key_set: set[str] = set()
    for window in planned_windows:
        if len(window.pending_row_ids) != len(window.sources):
            _log_stale_append(user_id=user_id, reason="invalid_planned_window")
            return False
        for pending_row_id, source in zip(window.pending_row_ids, window.sources, strict=True):
            if pending_row_id in expected_rows or source.source_key in planned_source_key_set:
                _log_stale_append(user_id=user_id, reason="duplicate_planned_source")
                return False
            expected_rows[pending_row_id] = (source.kind, source.id, window.lens_key)
            planned_source_keys.append(source.source_key)
            planned_source_key_set.add(source.source_key)

    if not expected_rows:
        return True

    pending_rows = (
        db.query(BriefingPendingSource)
        .filter(
            BriefingPendingSource.user_id == user_id,
            BriefingPendingSource.id.in_(expected_rows),
        )
        .with_for_update()
        .all()
    )
    actual_rows = {
        int(row.id): (str(row.source_kind), int(row.source_id), str(row.lens_key))
        for row in pending_rows
        if row.id is not None and row.source_id is not None and row.lens_key is not None
    }
    if actual_rows != expected_rows:
        _log_stale_append(user_id=user_id, reason="pending_ownership_changed")
        return False

    eligible_source_keys = eligible_unread_source_keys_for(
        db,
        user_id=user_id,
        source_keys=planned_source_keys,
    )
    if eligible_source_keys != set(planned_source_keys):
        _log_stale_append(user_id=user_id, reason="source_eligibility_changed")
        return False
    return True


def _persist_append_windows(
    db: Session,
    *,
    user_id: int,
    composed_windows: list[ComposedWindow[AppendWindow]],
) -> int:
    pending_row_ids: list[int] = []
    for composed in composed_windows:
        prepared = composed.prepared
        db.add(
            build_briefing_segment(
                lens_id=prepared.lens_id,
                user_id=user_id,
                segment=composed.segment,
                source_keys=[source.source_key for source in prepared.sources],
                event_groups=prepared.event_groups,
            )
        )
        pending_row_ids.extend(prepared.pending_row_ids)

    if pending_row_ids:
        deleted = (
            db.query(BriefingPendingSource)
            .filter(
                BriefingPendingSource.user_id == user_id,
                BriefingPendingSource.id.in_(pending_row_ids),
            )
            .delete(synchronize_session=False)
        )
        if deleted != len(pending_row_ids):
            raise RuntimeError("Briefing append publication lost pending-source ownership")
    return len(composed_windows)


def _append_window_shape(window: AppendWindow) -> tuple[tuple[int, ...], tuple[str, ...]]:
    return window.pending_row_ids, tuple(source.source_key for source in window.sources)


def _log_stale_append(*, user_id: int, reason: str) -> None:
    logger.info(
        "Briefing append publication plan became stale",
        extra={
            "component": "briefing",
            "operation": "publish_append",
            "item_id": user_id,
            "context_data": {"reason": reason},
        },
    )
