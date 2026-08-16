from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import Settings
from app.models.db import BriefingLens, BriefingSegment
from app.services.briefing.composer import plan_windows
from app.services.briefing.segments import build_briefing_segment
from app.services.briefing.sources import (
    BriefingSource,
    eligible_unread_source_keys_for,
    eligible_unread_sources_for_keys,
    read_source_keys,
)
from app.services.briefing.window_composition import ComposedWindow

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
    donors: tuple[CompactionDonor, ...]
    unread_source_keys: tuple[str, ...]
    replacement_source_keys: tuple[str, ...]
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
    window_source_limit = max(settings.briefing_news_window_max, 1) if tier == "news" else 1
    minimum_required = ceil(len(unique_unread_keys) / window_source_limit)
    return BriefingFragmentationMetrics(
        unique_unread_source_count=len(unique_unread_keys),
        window_source_limit=window_source_limit,
        minimum_required_segment_count=minimum_required,
        excess_fragmentation=max(len(source_key_groups) - minimum_required, 0),
    )


def prepare_compactions(
    db: Session,
    *,
    user_id: int,
    settings: Settings,
) -> list[CompactionPlan]:
    read_keys = read_source_keys(db, user_id=user_id)
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

    all_source_keys = _ordered_unread_source_keys(
        [segment for segments in segments_by_lens_id.values() for segment in segments],
        read_keys=read_keys,
    )
    eligible_source_keys = eligible_unread_source_keys_for(
        db,
        user_id=user_id,
        source_keys=all_source_keys,
    )
    for lens in lenses:
        if lens.id is None:
            continue
        segments = segments_by_lens_id[int(lens.id)]
        eligible_source_groups: list[list[str]] = []
        unavailable_source_keys: set[str] = set()
        repair_donors: list[BriefingSegment] = []
        for segment in segments:
            unread_keys = _segment_unread_source_keys(segment, read_keys=read_keys)
            eligible_keys = [key for key in unread_keys if key in eligible_source_keys]
            unavailable_keys = [key for key in unread_keys if key not in eligible_source_keys]
            if eligible_keys:
                eligible_source_groups.append(eligible_keys)
            if unavailable_keys:
                unavailable_source_keys.update(unavailable_keys)
                repair_donors.append(segment)
        fragmentation = briefing_fragmentation_metrics(
            eligible_source_groups,
            tier=str(lens.tier),
            read_keys=set(),
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
                    "unavailable_source_count": len(unavailable_source_keys),
                },
            },
        )
        regular_donors = _compaction_donors(
            segments,
            read_keys=read_keys,
            tier=str(lens.tier),
        )
        candidate_ids = {
            int(segment.id)
            for segment in (*repair_donors, *regular_donors)
            if segment.id is not None
        }
        donors = [
            segment
            for segment in segments
            if segment.id is not None and int(segment.id) in candidate_ids
        ]
        if not donors:
            continue
        donor_source_keys = _ordered_unread_source_keys(donors, read_keys=read_keys)
        replacement_source_keys = [key for key in donor_source_keys if key in eligible_source_keys]
        source_map = eligible_unread_sources_for_keys(
            db,
            user_id=user_id,
            source_keys=replacement_source_keys,
        )
        replacement_source_keys = [key for key in replacement_source_keys if key in source_map]
        sources = [source_map[key] for key in replacement_source_keys]
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
        if str(lens.tier) == "news" and not repair_donors and len(windows) >= len(donors):
            continue
        plans.append(
            CompactionPlan(
                lens_id=int(lens.id),
                donors=tuple(
                    CompactionDonor(
                        segment_id=int(segment.id),
                        source_keys=tuple(str(key) for key in (segment.source_keys or [])),
                    )
                    for segment in donors
                    if segment.id is not None
                ),
                unread_source_keys=tuple(donor_source_keys),
                replacement_source_keys=tuple(replacement_source_keys),
                windows=windows,
            )
        )
    return plans


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
    for plan in plans:
        donor_ids = [donor.segment_id for donor in plan.donors]
        donors = (
            db.query(BriefingSegment)
            .filter(BriefingSegment.id.in_(donor_ids))
            .order_by(BriefingSegment.created_at.desc(), BriefingSegment.id.desc())
            .with_for_update()
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
        current_eligible_keys = eligible_unread_source_keys_for(
            db,
            user_id=user_id,
            source_keys=unread_keys,
        )
        current_replacement_source_keys = tuple(
            key for key in unread_keys if key in current_eligible_keys
        )
        if current_replacement_source_keys != plan.replacement_source_keys:
            logger.info(
                "Briefing compaction source eligibility became stale",
                extra={
                    "component": "briefing",
                    "operation": "persist_compaction",
                    "item_id": user_id,
                    "context_data": {
                        "lens_id": plan.lens_id,
                        "planned_replacement_source_count": len(plan.replacement_source_keys),
                        "current_replacement_source_count": len(current_replacement_source_keys),
                    },
                },
            )
            continue
        lens_windows = composed_by_lens.get(plan.lens_id, [])
        replacement_keys = {
            source.source_key for window in lens_windows for source in window.prepared.sources
        }
        if replacement_keys != set(plan.replacement_source_keys) or len(lens_windows) != len(
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
                        "source_count": len(plan.replacement_source_keys),
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
                    "source_count": len(plan.replacement_source_keys),
                    "unavailable_source_count": len(plan.unread_source_keys)
                    - len(plan.replacement_source_keys),
                    "coverage_complete": True,
                },
            },
        )
    return compacted


def _compaction_donors(
    segments: list[BriefingSegment],
    *,
    read_keys: set[str],
    tier: str,
) -> list[BriefingSegment]:
    if tier != "news":
        donors: list[BriefingSegment] = []
        for segment in segments:
            source_keys = set(segment.source_keys or [])
            if len(source_keys) > 1 and source_keys - read_keys:
                donors.append(segment)
        return donors

    return [
        segment for segment in segments if 0 < len(set(segment.source_keys or []) - read_keys) <= 2
    ]


def _segment_unread_source_keys(
    segment: BriefingSegment,
    *,
    read_keys: set[str],
) -> list[str]:
    return [
        str(raw_key) for raw_key in (segment.source_keys or []) if str(raw_key) not in read_keys
    ]


def _ordered_unread_source_keys(
    donors: list[BriefingSegment],
    *,
    read_keys: set[str],
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for donor in donors:
        for key in _segment_unread_source_keys(donor, read_keys=read_keys):
            if key in seen:
                continue
            seen.add(key)
            ordered.append(key)
    return ordered
