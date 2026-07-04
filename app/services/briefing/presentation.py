from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.api.briefing import (
    BriefingBlockDto,
    BriefingIndexResponse,
    BriefingLensResponse,
    BriefingLensSummary,
    BriefingSegmentDto,
    BriefingSourceDto,
)
from app.models.contracts import BriefingTier
from app.models.db import BriefingLens, BriefingSegment
from app.services.briefing.refresh import ensure_state
from app.services.briefing.sources import read_source_keys_for, sources_for_keys


def get_briefing_index(db: Session, *, user_id: int) -> BriefingIndexResponse:
    state = ensure_state(db, user_id=user_id)
    lenses = (
        db.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id)
        .filter(BriefingLens.status == "active")
        .order_by(BriefingLens.position.asc(), BriefingLens.id.asc())
        .all()
    )
    active_segments = _active_segments(db, user_id=user_id)
    source_keys_by_lens_id = _source_keys_by_lens_id(active_segments)
    all_source_keys = sorted({key for keys in source_keys_by_lens_id.values() for key in keys})
    read_keys = read_source_keys_for(db, user_id=user_id, source_keys=all_source_keys)
    segment_counts_by_lens_id = _segment_counts_by_lens_id(active_segments)
    summaries = [
        _lens_summary(
            lens=lens,
            read_keys=read_keys,
            source_keys=source_keys_by_lens_id.get(int(lens.id or 0), set()),
            segment_count=segment_counts_by_lens_id.get(int(lens.id or 0), 0),
        )
        for lens in lenses
    ]
    generated_at = max(
        [segment.created_at for segment in active_segments if segment.created_at],
        default=None,
    )
    return BriefingIndexResponse(
        version=int(state.version or 0),
        masthead_title=str(state.masthead_title),
        masthead_deck=str(state.masthead_deck),
        generated_at=generated_at,
        lenses=summaries,
    )


def get_briefing_lens(
    db: Session,
    *,
    user_id: int,
    lens_key: str,
) -> BriefingLensResponse | None:
    lens = (
        db.query(BriefingLens)
        .filter(
            BriefingLens.user_id == user_id,
            BriefingLens.key == lens_key,
            BriefingLens.status == "active",
        )
        .first()
    )
    if lens is None:
        return None
    state = ensure_state(db, user_id=user_id)
    segments = (
        db.query(BriefingSegment)
        .filter(BriefingSegment.lens_id == lens.id)
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .order_by(BriefingSegment.created_at.desc(), BriefingSegment.id.desc())
        .all()
    )
    ordered_keys = _deduped_source_keys(segments)
    read_keys = read_source_keys_for(db, user_id=user_id, source_keys=ordered_keys)
    source_map = sources_for_keys(db, user_id=user_id, source_keys=ordered_keys)
    source_dtos = [
        BriefingSourceDto.model_validate(source_map[key].dto(read=key in read_keys))
        for key in ordered_keys
        if key in source_map
    ]
    return BriefingLensResponse(
        version=int(state.version or 0),
        lens=_lens_summary(
            lens=lens,
            read_keys=read_keys,
            source_keys=set(ordered_keys),
            segment_count=len(segments),
        ),
        segments=[_segment_dto(segment) for segment in segments],
        sources=source_dtos,
    )


def _lens_summary(
    *,
    lens: BriefingLens,
    read_keys: set[str],
    source_keys: set[str],
    segment_count: int,
) -> BriefingLensSummary:
    return BriefingLensSummary(
        key=str(lens.key),
        tier=BriefingTier(str(lens.tier)),
        title=str(lens.title),
        deck=str(lens.deck or ""),
        position=int(lens.position or 0),
        segment_count=segment_count,
        unread_source_count=len(source_keys - read_keys),
    )


def _segment_dto(segment: BriefingSegment) -> BriefingSegmentDto:
    if segment.id is None or segment.created_at is None:
        raise ValueError("Cannot serialize an unpersisted briefing segment")
    return BriefingSegmentDto(
        id=int(segment.id),
        created_at=segment.created_at,
        status=str(segment.status),
        narration_text=str(segment.narration_text or ""),
        blocks=[BriefingBlockDto.model_validate(block) for block in (segment.blocks or [])],
        source_keys=[str(key) for key in (segment.source_keys or [])],
    )


def _active_segments(db: Session, *, user_id: int) -> list[BriefingSegment]:
    return (
        db.query(BriefingSegment)
        .filter(BriefingSegment.user_id == user_id)
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .all()
    )


def _source_keys_by_lens_id(segments: list[BriefingSegment]) -> dict[int, set[str]]:
    source_keys_by_lens_id: dict[int, set[str]] = {}
    for segment in segments:
        if segment.lens_id is None:
            continue
        keys = source_keys_by_lens_id.setdefault(int(segment.lens_id), set())
        keys.update(str(key) for key in (segment.source_keys or []))
    return source_keys_by_lens_id


def _segment_counts_by_lens_id(segments: list[BriefingSegment]) -> dict[int, int]:
    counts_by_lens_id: dict[int, int] = {}
    for segment in segments:
        if segment.lens_id is None:
            continue
        lens_id = int(segment.lens_id)
        counts_by_lens_id[lens_id] = counts_by_lens_id.get(lens_id, 0) + 1
    return counts_by_lens_id


def _deduped_source_keys(segments: list[BriefingSegment]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for segment in segments:
        for raw_key in segment.source_keys or []:
            key = str(raw_key)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(key)
    return ordered
