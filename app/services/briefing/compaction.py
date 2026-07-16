from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import Settings
from app.models.db import BriefingLens, BriefingSegment, BriefingState
from app.services.briefing.composer import LayoutGenerator, plan_windows
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


@dataclass(frozen=True)
class BriefingFragmentationMetrics:
    unique_unread_source_count: int
    window_source_limit: int
    minimum_required_segment_count: int
    excess_fragmentation: int


def briefing_fragmentation_metrics(
    source_key_groups: list[list[str]],
    *,
    tier: str,
    read_keys: set[str],
    settings: Settings,
) -> BriefingFragmentationMetrics:
    unique_unread_keys = {
        str(source_key)
        for source_keys in source_key_groups
        for source_key in source_keys
        if str(source_key) not in read_keys
    }
    window_source_limit = max(
        settings.briefing_news_window_max if tier == "news" else settings.briefing_window_max,
        1,
    )
    minimum_required = ceil(len(unique_unread_keys) / window_source_limit)
    return BriefingFragmentationMetrics(
        unique_unread_source_count=len(unique_unread_keys),
        window_source_limit=window_source_limit,
        minimum_required_segment_count=minimum_required,
        excess_fragmentation=max(len(source_key_groups) - minimum_required, 0),
    )


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
) -> list[CompactionPlan]:
    read_keys = read_source_keys(db, user_id=user_id)
    starting_version = _state_version(db, user_id=user_id)
    plans: list[CompactionPlan] = []
    lenses = (
        db.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id, BriefingLens.status == "active")
        .order_by(BriefingLens.position.asc(), BriefingLens.id.asc())
        .all()
    )
    lens_ids = [int(lens.id) for lens in lenses if lens.id is not None]
    segments_by_lens_id: dict[int, list[BriefingSegment]] = {lens_id: [] for lens_id in lens_ids}
    if lens_ids:
        segment_rows = (
            db.query(BriefingSegment)
            .filter(BriefingSegment.lens_id.in_(lens_ids))
            .filter(BriefingSegment.status.in_(("active", "degraded")))
            .order_by(
                BriefingSegment.lens_id.asc(),
                BriefingSegment.created_at.desc(),
                BriefingSegment.id.desc(),
            )
            .all()
        )
        for segment in segment_rows:
            if segment.lens_id is not None:
                segments_by_lens_id[int(segment.lens_id)].append(segment)

    prepared_donors: list[tuple[BriefingLens, list[BriefingSegment], list[str]]] = []
    all_source_keys: list[str] = []
    seen_source_keys: set[str] = set()
    for lens in lenses:
        if lens.id is None:
            continue
        segments = segments_by_lens_id[int(lens.id)]
        fragmentation = briefing_fragmentation_metrics(
            [list(segment.source_keys or []) for segment in segments],
            tier=str(lens.tier),
            read_keys=read_keys,
            settings=settings,
        )
        logger.info(
            "Briefing Lens fragmentation measured",
            extra={
                "component": "briefing",
                "operation": "measure_fragmentation",
                "item_id": user_id,
                "context_data": {
                    "lens_key": str(lens.key),
                    "active_segment_count": len(segments),
                    "unique_unread_source_count": fragmentation.unique_unread_source_count,
                    "window_source_limit": fragmentation.window_source_limit,
                    "minimum_required_segment_count": (
                        fragmentation.minimum_required_segment_count
                    ),
                    "excess_fragmentation": fragmentation.excess_fragmentation,
                },
            },
        )
        donors = _compaction_donors(
            segments,
            read_keys=read_keys,
        )
        if not donors:
            continue
        source_keys = _ordered_unread_source_keys(donors, read_keys=read_keys)
        if len(source_keys) < settings.briefing_window_min:
            continue
        prepared_donors.append((lens, donors, source_keys))
        for source_key in source_keys:
            if source_key not in seen_source_keys:
                seen_source_keys.add(source_key)
                all_source_keys.append(source_key)

    source_map = sources_for_keys(db, user_id=user_id, source_keys=all_source_keys)
    for lens, donors, source_keys in prepared_donors:
        assert lens.id is not None
        lens_id = lens.id
        missing_source_keys = set(source_keys) - set(source_map)
        if missing_source_keys:
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
                        "resolved_source_count": len(source_keys) - len(missing_source_keys),
                    },
                },
            )
            continue
        sources = [source_map[key] for key in source_keys]
        windows = tuple(
            CompactionWindow(
                lens_id=lens_id,
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
                lens_id=lens_id,
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
    layout_generator: LayoutGenerator | None = None,
) -> list[ComposedWindow[CompactionWindow]]:
    windows = [window for plan in plans for window in plan.windows]
    return compose_windows(
        windows,
        user_id=user_id,
        task_id=task_id,
        use_llm=use_llm,
        settings=settings,
        layout_generator=layout_generator,
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
) -> list[BriefingSegment]:
    small = [
        segment for segment in segments if 0 < len(set(segment.source_keys or []) - read_keys) <= 2
    ]
    return small if len(small) >= 3 else []


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
