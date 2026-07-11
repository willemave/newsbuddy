from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import ceil

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import Settings
from app.models.db import BriefingLens, BriefingSegment, BriefingState
from app.services.briefing.composer import plan_windows
from app.services.briefing.segments import build_briefing_segment
from app.services.briefing.sources import (
    BriefingSource,
    read_source_keys,
    sources_for_keys,
)
from app.services.briefing.window_composition import ComposedWindow, compose_windows

logger = get_logger(__name__)


@dataclass(frozen=True)
class CompactionDonor:
    segment_id: int
    source_keys: tuple[str, ...]


@dataclass(frozen=True)
class CompactionWindow:
    lens_id: int
    lens_key: str
    lens_title: str
    tier: str
    window_index: int
    sources: tuple[BriefingSource, ...]


@dataclass(frozen=True)
class CompactionPlan:
    lens_id: int
    starting_version: int
    donors: tuple[CompactionDonor, ...]
    unread_source_keys: tuple[str, ...]
    windows: tuple[CompactionWindow, ...]


def compact_fragmented_lenses(
    db: Session,
    *,
    user_id: int,
    task_id: int | None,
    use_llm: bool,
    settings: Settings,
) -> int:
    plans = prepare_compactions(db, user_id=user_id, settings=settings)
    composed = compose_compactions(
        plans,
        user_id=user_id,
        task_id=task_id,
        use_llm=use_llm,
        settings=settings,
    )
    return persist_compactions(
        db,
        user_id=user_id,
        plans=plans,
        composed_windows=composed,
    )


def prepare_compactions(
    db: Session,
    *,
    user_id: int,
    settings: Settings,
    reserved_segment_counts: Mapping[int, int] | None = None,
) -> list[CompactionPlan]:
    reserved_segment_counts = reserved_segment_counts or {}
    read_keys = read_source_keys(db, user_id=user_id)
    starting_version = _state_version(db, user_id=user_id)
    plans: list[CompactionPlan] = []
    lenses = (
        db.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id, BriefingLens.status == "active")
        .order_by(BriefingLens.position.asc(), BriefingLens.id.asc())
        .all()
    )
    for lens in lenses:
        if lens.id is None:
            continue
        segments = (
            db.query(BriefingSegment)
            .filter(BriefingSegment.lens_id == lens.id)
            .filter(BriefingSegment.status.in_(("active", "degraded")))
            .order_by(BriefingSegment.created_at.desc(), BriefingSegment.id.desc())
            .all()
        )
        donors = _compaction_donors(
            segments,
            read_keys=read_keys,
            settings=settings,
            tier=str(lens.tier),
            reserved_segment_count=reserved_segment_counts.get(int(lens.id), 0),
        )
        if not donors:
            continue
        source_keys = _ordered_unread_source_keys(donors, read_keys=read_keys)
        if len(source_keys) < settings.briefing_window_min:
            continue
        source_map = sources_for_keys(db, user_id=user_id, source_keys=source_keys)
        if set(source_map) != set(source_keys):
            logger.warning(
                "Briefing compaction skipped because donor sources could not be resolved",
                extra={
                    "component": "briefing",
                    "operation": "prepare_compaction",
                    "item_id": user_id,
                    "context_data": {
                        "lens_key": str(lens.key),
                        "donor_count": len(donors),
                        "source_count": len(source_keys),
                        "resolved_source_count": len(source_map),
                    },
                },
            )
            continue
        sources = [source_map[key] for key in source_keys]
        windows = tuple(
            CompactionWindow(
                lens_id=int(lens.id),
                lens_key=str(lens.key),
                lens_title=str(lens.title),
                tier=str(lens.tier),
                window_index=window_index,
                sources=tuple(window),
            )
            for window_index, window in enumerate(
                plan_windows(sources, tier=str(lens.tier), settings=settings),
                start=1,
            )
        )
        plans.append(
            CompactionPlan(
                lens_id=int(lens.id),
                starting_version=starting_version,
                donors=tuple(
                    CompactionDonor(
                        segment_id=int(segment.id),
                        source_keys=tuple(str(key) for key in (segment.source_keys or [])),
                    )
                    for segment in donors
                    if segment.id is not None
                ),
                unread_source_keys=tuple(source_keys),
                windows=windows,
            )
        )
    return plans


def compose_compactions(
    plans: list[CompactionPlan],
    *,
    user_id: int,
    task_id: int | None,
    use_llm: bool,
    settings: Settings,
) -> list[ComposedWindow[CompactionWindow]]:
    windows = [window for plan in plans for window in plan.windows]
    return compose_windows(
        windows,
        user_id=user_id,
        task_id=task_id,
        use_llm=use_llm,
        settings=settings,
    )


def persist_compactions(
    db: Session,
    *,
    user_id: int,
    plans: list[CompactionPlan],
    composed_windows: list[ComposedWindow[CompactionWindow]],
) -> int:
    composed_by_lens: dict[int, list[ComposedWindow[CompactionWindow]]] = {}
    for composed_window in composed_windows:
        composed_by_lens.setdefault(composed_window.prepared.lens_id, []).append(composed_window)

    compacted = 0
    current_read_keys = read_source_keys(db, user_id=user_id)
    current_version = _state_version(db, user_id=user_id)
    for plan in plans:
        if current_version != plan.starting_version:
            logger.info(
                "Briefing compaction plan version became stale",
                extra={
                    "component": "briefing",
                    "operation": "persist_compaction",
                    "item_id": user_id,
                    "context_data": {
                        "lens_id": plan.lens_id,
                        "starting_version": plan.starting_version,
                        "current_version": current_version,
                    },
                },
            )
            continue
        donor_ids = [donor.segment_id for donor in plan.donors]
        donors = (
            db.query(BriefingSegment)
            .filter(BriefingSegment.id.in_(donor_ids))
            .order_by(BriefingSegment.created_at.desc(), BriefingSegment.id.desc())
            .all()
        )
        expected_by_id = {donor.segment_id: donor for donor in plan.donors}
        donors_unchanged = len(donors) == len(plan.donors) and all(
            segment.status in ("active", "degraded")
            and tuple(str(key) for key in (segment.source_keys or []))
            == expected_by_id[int(segment.id)].source_keys
            for segment in donors
            if segment.id is not None
        )
        unread_keys = _ordered_unread_source_keys(donors, read_keys=current_read_keys)
        if not donors_unchanged or tuple(unread_keys) != plan.unread_source_keys:
            logger.info(
                "Briefing compaction plan became stale",
                extra={
                    "component": "briefing",
                    "operation": "persist_compaction",
                    "item_id": user_id,
                    "context_data": {"lens_id": plan.lens_id, "donor_count": len(plan.donors)},
                },
            )
            continue
        lens_windows = composed_by_lens.get(plan.lens_id, [])
        replacement_keys = {
            source.source_key for window in lens_windows for source in window.prepared.sources
        }
        if replacement_keys != set(plan.unread_source_keys) or len(lens_windows) != len(
            plan.windows
        ):
            logger.error(
                "Briefing compaction replacement coverage was incomplete",
                extra={
                    "component": "briefing",
                    "operation": "persist_compaction",
                    "item_id": user_id,
                    "context_data": {
                        "lens_id": plan.lens_id,
                        "source_count": len(plan.unread_source_keys),
                        "replacement_source_count": len(replacement_keys),
                    },
                },
            )
            continue
        for window in lens_windows:
            segment = window.segment
            db.add(
                build_briefing_segment(
                    lens_id=plan.lens_id,
                    user_id=user_id,
                    segment=segment,
                    source_keys=[source.source_key for source in window.prepared.sources],
                    extra_warnings=("compaction_segment",),
                )
            )
        for donor in donors:
            donor.status = "compacted"
        compacted += len(donors)
        logger.info(
            "Briefing compaction coverage committed",
            extra={
                "component": "briefing",
                "operation": "persist_compaction",
                "item_id": user_id,
                "context_data": {
                    "lens_id": plan.lens_id,
                    "donor_count": len(donors),
                    "replacement_count": len(lens_windows),
                    "source_count": len(plan.unread_source_keys),
                    "coverage_complete": True,
                },
            },
        )
    return compacted


def _compaction_donors(
    segments: list[BriefingSegment],
    *,
    read_keys: set[str],
    settings: Settings,
    tier: str,
    reserved_segment_count: int = 0,
) -> list[BriefingSegment]:
    maximum = max(settings.briefing_max_segments_per_lens - reserved_segment_count, 0)
    if len(segments) <= maximum:
        small = [
            segment
            for segment in segments
            if 0 < len(set(segment.source_keys or []) - read_keys) <= 2
        ]
        return small if len(small) >= 3 else []

    keep_count = max(maximum - 1, 0)
    while keep_count > 0:
        donors = segments[keep_count:]
        source_count = len(_ordered_unread_source_keys(donors, read_keys=read_keys))
        window_size = (
            settings.briefing_news_window_max if tier == "news" else settings.briefing_window_max
        )
        replacement_count = ceil(source_count / max(window_size, 1))
        if keep_count + replacement_count <= maximum:
            return donors
        keep_count -= 1
    return segments


def _ordered_unread_source_keys(
    donors: list[BriefingSegment],
    *,
    read_keys: set[str],
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for donor in donors:
        for raw_key in donor.source_keys or []:
            key = str(raw_key)
            if key in read_keys or key in seen:
                continue
            seen.add(key)
            ordered.append(key)
    return ordered


def _state_version(db: Session, *, user_id: int) -> int:
    state = db.query(BriefingState).filter(BriefingState.user_id == user_id).one_or_none()
    return int(state.version or 0) if state is not None else 0
