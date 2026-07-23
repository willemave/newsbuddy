from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter

from sqlalchemy import or_, text
from sqlalchemy.orm import Session, load_only

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.models.api.briefing import (
    BriefingBlockDto,
    BriefingIndexResponse,
    BriefingLensResponse,
    BriefingLensSummary,
    BriefingSegmentDto,
    BriefingSourceDto,
)
from app.models.contracts import BriefingTier
from app.models.db import BriefingLens, BriefingPendingSource, BriefingSegment, BriefingState
from app.services.briefing.first_run import get_first_run_progress, get_first_run_validator
from app.services.briefing.refresh import ensure_state
from app.services.briefing.sources import read_source_keys_for, sources_for_keys

logger = get_logger(__name__)

BRIEFING_LENS_PAGE_MAX = 12
BRIEFING_LENS_SUMMARY_LOAD = load_only(
    BriefingLens.id,
    BriefingLens.key,
    BriefingLens.tier,
    BriefingLens.title,
    BriefingLens.deck,
    BriefingLens.position,
)
BRIEFING_SEGMENT_DTO_LOAD = load_only(
    BriefingSegment.id,
    BriefingSegment.blocks,
    BriefingSegment.narration_text,
    BriefingSegment.source_keys,
    BriefingSegment.status,
    BriefingSegment.created_at,
)


@dataclass(frozen=True)
class BriefingIndexValidator:
    version: int
    first_run_id: int
    first_run_revision: int


@dataclass(frozen=True)
class _ActiveSegmentSummary:
    lens_id: int
    source_keys: tuple[str, ...]
    created_at: datetime | None


@dataclass(frozen=True)
class _LensSegmentAggregate:
    segment_count: int
    source_keys: tuple[str, ...]
    cursor_exists: bool
    cursor_matches: bool


@dataclass(frozen=True)
class _LensCursor:
    lens_id: int
    segment_id: int
    created_at: datetime


class InvalidBriefingLensCursor(ValueError):
    pass


class StaleBriefingLensCursor(ValueError):
    pass


def get_briefing_index_validator(db: Session, *, user_id: int) -> BriefingIndexValidator:
    started_at = perf_counter()
    state_started_at = perf_counter()
    version = db.query(BriefingState.version).filter(BriefingState.user_id == user_id).scalar()
    state_query_ms = _elapsed_ms(state_started_at)
    first_run_started_at = perf_counter()
    first_run = get_first_run_validator(db, user_id=user_id)
    first_run_query_ms = _elapsed_ms(first_run_started_at)
    validator = BriefingIndexValidator(
        version=int(version or 0),
        first_run_id=first_run.run_id if first_run else 0,
        first_run_revision=first_run.revision if first_run else 0,
    )
    logger.info(
        "Briefing index validator presented",
        extra=build_log_extra(
            component="briefing",
            operation="present_index_validator",
            event_name="briefing.presentation.index_validator",
            status="completed",
            duration_ms=_elapsed_ms(started_at),
            user_id=user_id,
            context_data={
                "state_query_ms": state_query_ms,
                "first_run_query_ms": first_run_query_ms,
                "has_first_run": first_run is not None,
            },
        ),
    )
    return validator


def get_briefing_index(db: Session, *, user_id: int) -> BriefingIndexResponse:
    started_at = perf_counter()
    state = ensure_state(db, user_id=user_id)
    lens_query_started_at = perf_counter()
    lenses = (
        db.query(BriefingLens)
        .options(BRIEFING_LENS_SUMMARY_LOAD)
        .filter(BriefingLens.user_id == user_id)
        .filter(BriefingLens.status == "active")
        .order_by(BriefingLens.position.asc(), BriefingLens.id.asc())
        .all()
    )
    lens_query_ms = _elapsed_ms(lens_query_started_at)
    segment_query_started_at = perf_counter()
    active_segments = _active_segments(db, user_id=user_id)
    segment_query_ms = _elapsed_ms(segment_query_started_at)
    source_keys_by_lens_id = _source_keys_by_lens_id(active_segments)
    all_source_keys = sorted({key for keys in source_keys_by_lens_id.values() for key in keys})
    read_query_started_at = perf_counter()
    read_keys = read_source_keys_for(db, user_id=user_id, source_keys=all_source_keys)
    read_query_ms = _elapsed_ms(read_query_started_at)
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
    readable_summaries = [summary for summary in summaries if summary.segment_count > 0]
    first_run_started_at = perf_counter()
    first_run = get_first_run_progress(
        db,
        user_id=user_id,
        ready_category_keys=[summary.key for summary in readable_summaries],
    )
    first_run_query_ms = _elapsed_ms(first_run_started_at)
    if first_run is not None:
        pending_lens_keys = {
            str(lens_key)
            for (lens_key,) in db.query(BriefingPendingSource.lens_key)
            .filter(
                BriefingPendingSource.user_id == user_id,
                BriefingPendingSource.lens_key.is_not(None),
            )
            .distinct()
            .all()
        }
        summaries = [
            summary
            for summary in summaries
            if summary.segment_count > 0 or summary.key in pending_lens_keys
        ]
    generated_at = max(
        [segment.created_at for segment in active_segments if segment.created_at],
        default=None,
    )
    response = BriefingIndexResponse(
        version=int(state.version or 0),
        masthead_title=str(state.masthead_title),
        masthead_deck=str(state.masthead_deck),
        generated_at=generated_at,
        lenses=summaries,
        first_run=first_run,
    )
    logger.info(
        "Briefing index presented",
        extra=build_log_extra(
            component="briefing",
            operation="present_index",
            event_name="briefing.presentation.index",
            status="completed",
            duration_ms=_elapsed_ms(started_at),
            user_id=user_id,
            context_data={
                "lens_query_ms": lens_query_ms,
                "segment_query_ms": segment_query_ms,
                "read_query_ms": read_query_ms,
                "first_run_query_ms": first_run_query_ms,
                "lens_count": len(lenses),
                "segment_count": len(active_segments),
                "source_key_count": len(all_source_keys),
                "read_key_count": len(read_keys),
            },
        ),
    )
    return response


def get_briefing_lens(
    db: Session,
    *,
    user_id: int,
    lens_key: str,
    limit: int | None = None,
    cursor: str | None = None,
) -> BriefingLensResponse | None:
    started_at = perf_counter()
    lens_query_started_at = perf_counter()
    lens = (
        db.query(BriefingLens)
        .options(BRIEFING_LENS_SUMMARY_LOAD)
        .filter(
            BriefingLens.user_id == user_id,
            BriefingLens.key == lens_key,
            BriefingLens.status == "active",
        )
        .first()
    )
    lens_query_ms = _elapsed_ms(lens_query_started_at)
    if lens is None:
        return None
    assert lens.id is not None
    lens_id = lens.id
    state_query_started_at = perf_counter()
    state = ensure_state(db, user_id=user_id)
    state_query_ms = _elapsed_ms(state_query_started_at)
    is_paged = limit is not None or cursor is not None
    next_cursor: str | None = None
    has_more = False
    segment_metadata_query_ms = 0.0
    if is_paged:
        page_limit = BRIEFING_LENS_PAGE_MAX if limit is None else limit
        if not 1 <= page_limit <= BRIEFING_LENS_PAGE_MAX:
            raise InvalidBriefingLensCursor("Briefing page limit is out of range")
        decoded_cursor = _decode_lens_cursor(cursor) if cursor else None
        if decoded_cursor is not None and decoded_cursor.lens_id != lens_id:
            raise InvalidBriefingLensCursor("Briefing cursor belongs to another Lens")
        segment_metadata_query_started_at = perf_counter()
        aggregate = _lens_segment_aggregate(
            db,
            lens_id=lens_id,
            cursor=decoded_cursor,
        )
        segment_metadata_query_ms = _elapsed_ms(segment_metadata_query_started_at)
        if decoded_cursor is not None and not aggregate.cursor_exists:
            raise StaleBriefingLensCursor("Briefing cursor anchor is no longer active")
        if decoded_cursor is not None and not aggregate.cursor_matches:
            raise InvalidBriefingLensCursor("Briefing cursor anchor does not match")
        segment_body_query_started_at = perf_counter()
        segments, has_more = _briefing_segments_page(
            db,
            lens_id=lens_id,
            limit=page_limit,
            cursor=decoded_cursor,
        )
        all_ordered_keys = list(aggregate.source_keys)
        segment_count = aggregate.segment_count
        if has_more and segments:
            next_cursor = _encode_lens_cursor(lens_id=lens_id, segment=segments[-1])
    else:
        segment_body_query_started_at = perf_counter()
        segments = _briefing_segments_for_lens(db, lens_id=lens_id)
        all_ordered_keys = _deduped_source_keys(segments)
        segment_count = len(segments)
    segment_body_query_ms = _elapsed_ms(segment_body_query_started_at)

    page_ordered_keys = _deduped_source_keys(segments)
    read_query_started_at = perf_counter()
    read_keys = read_source_keys_for(db, user_id=user_id, source_keys=all_ordered_keys)
    read_query_ms = _elapsed_ms(read_query_started_at)
    source_query_started_at = perf_counter()
    source_map = sources_for_keys(
        db,
        user_id=user_id,
        source_keys=page_ordered_keys,
        include_briefing_context=False,
    )
    source_query_ms = _elapsed_ms(source_query_started_at)
    dto_started_at = perf_counter()
    source_dtos = [
        BriefingSourceDto.model_validate(source_map[key].dto(read=key in read_keys))
        for key in page_ordered_keys
        if key in source_map
    ]
    response = BriefingLensResponse(
        version=int(state.version or 0),
        lens=_lens_summary(
            lens=lens,
            read_keys=read_keys,
            source_keys=set(all_ordered_keys),
            segment_count=segment_count,
        ),
        segments=[_segment_dto(segment) for segment in segments],
        sources=source_dtos,
        next_cursor=next_cursor,
        has_more=has_more,
    )
    dto_ms = _elapsed_ms(dto_started_at)
    logger.info(
        "Briefing Lens presented",
        extra=build_log_extra(
            component="briefing",
            operation="present_lens",
            event_name="briefing.presentation.lens",
            status="completed",
            duration_ms=_elapsed_ms(started_at),
            user_id=user_id,
            context_data={
                "request_type": (
                    "legacy_full"
                    if not is_paged
                    else "continuation_page"
                    if cursor
                    else "foreground_first_page"
                ),
                "lens_query_ms": lens_query_ms,
                "state_query_ms": state_query_ms,
                "segment_metadata_query_ms": segment_metadata_query_ms,
                "segment_body_query_ms": segment_body_query_ms,
                "read_query_ms": read_query_ms,
                "source_query_ms": source_query_ms,
                "dto_ms": dto_ms,
                "segment_count": len(segments),
                "total_segment_count": segment_count,
                "unique_source_key_count": len(page_ordered_keys),
                "returned_source_count": len(source_dtos),
                "has_more": has_more,
            },
        ),
    )
    return response


def _briefing_segments_for_lens(db: Session, *, lens_id: int) -> list[BriefingSegment]:
    return (
        db.query(BriefingSegment)
        .options(BRIEFING_SEGMENT_DTO_LOAD)
        .filter(BriefingSegment.lens_id == lens_id)
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .order_by(BriefingSegment.created_at.desc(), BriefingSegment.id.desc())
        .all()
    )


def _lens_segment_aggregate(
    db: Session,
    *,
    lens_id: int,
    cursor: _LensCursor | None,
) -> _LensSegmentAggregate:
    row = db.execute(
        text(
            """
            SELECT
                count(DISTINCT segment.id) AS segment_count,
                COALESCE(
                    array_agg(DISTINCT source_key.value)
                        FILTER (WHERE source_key.value IS NOT NULL),
                    ARRAY[]::text[]
                ) AS source_keys,
                count(*) FILTER (WHERE segment.id = :cursor_segment_id) > 0
                    AS cursor_exists,
                count(*) FILTER (
                    WHERE segment.id = :cursor_segment_id
                      AND segment.created_at = :cursor_created_at
                ) > 0 AS cursor_matches
            FROM briefing_segments AS segment
            LEFT JOIN LATERAL jsonb_array_elements_text(segment.source_keys)
                AS source_key(value) ON TRUE
            WHERE segment.lens_id = :lens_id
              AND segment.status IN ('active', 'degraded')
            """
        ),
        {
            "lens_id": lens_id,
            "cursor_segment_id": cursor.segment_id if cursor else 0,
            "cursor_created_at": cursor.created_at if cursor else datetime.min,
        },
    ).one()
    return _LensSegmentAggregate(
        segment_count=int(row.segment_count or 0),
        source_keys=tuple(str(key) for key in (row.source_keys or [])),
        cursor_exists=bool(row.cursor_exists),
        cursor_matches=bool(row.cursor_matches),
    )


def _briefing_segments_page(
    db: Session,
    *,
    lens_id: int,
    limit: int,
    cursor: _LensCursor | None,
) -> tuple[list[BriefingSegment], bool]:
    query = (
        db.query(BriefingSegment)
        .options(BRIEFING_SEGMENT_DTO_LOAD)
        .filter(BriefingSegment.lens_id == lens_id)
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .order_by(BriefingSegment.created_at.desc(), BriefingSegment.id.desc())
    )
    if cursor is not None:
        query = query.filter(
            or_(
                BriefingSegment.created_at < cursor.created_at,
                (
                    (BriefingSegment.created_at == cursor.created_at)
                    & (BriefingSegment.id < cursor.segment_id)
                ),
            )
        )
    rows = query.limit(limit + 1).all()
    return rows[:limit], len(rows) > limit


def _encode_lens_cursor(*, lens_id: int, segment: BriefingSegment) -> str:
    if segment.id is None or segment.created_at is None:
        raise ValueError("Cannot paginate an unpersisted briefing segment")
    payload = json.dumps(
        {
            "lens_id": lens_id,
            "segment_id": segment.id,
            "created_at": segment.created_at.isoformat(timespec="microseconds"),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_lens_cursor(value: str) -> _LensCursor:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding))
        lens_id = int(payload["lens_id"])
        segment_id = int(payload["segment_id"])
        created_at = datetime.fromisoformat(str(payload["created_at"]))
    except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidBriefingLensCursor("Malformed Briefing cursor") from exc
    if lens_id <= 0 or segment_id <= 0 or created_at.tzinfo is not None:
        raise InvalidBriefingLensCursor("Malformed Briefing cursor")
    return _LensCursor(lens_id=lens_id, segment_id=segment_id, created_at=created_at)


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


def _active_segments(db: Session, *, user_id: int) -> list[_ActiveSegmentSummary]:
    rows = (
        db.query(
            BriefingSegment.lens_id,
            BriefingSegment.source_keys,
            BriefingSegment.created_at,
        )
        .filter(BriefingSegment.user_id == user_id)
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .all()
    )
    return [
        _ActiveSegmentSummary(
            lens_id=int(row.lens_id),
            source_keys=tuple(str(key) for key in (row.source_keys or [])),
            created_at=row.created_at,
        )
        for row in rows
    ]


def _source_keys_by_lens_id(
    segments: list[_ActiveSegmentSummary],
) -> dict[int, set[str]]:
    source_keys_by_lens_id: dict[int, set[str]] = {}
    for segment in segments:
        keys = source_keys_by_lens_id.setdefault(segment.lens_id, set())
        keys.update(segment.source_keys)
    return source_keys_by_lens_id


def _segment_counts_by_lens_id(segments: list[_ActiveSegmentSummary]) -> dict[int, int]:
    counts_by_lens_id: dict[int, int] = {}
    for segment in segments:
        counts_by_lens_id[segment.lens_id] = counts_by_lens_id.get(segment.lens_id, 0) + 1
    return counts_by_lens_id


def _deduped_source_keys(
    segments: Iterable[BriefingSegment],
) -> list[str]:
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


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 2)
