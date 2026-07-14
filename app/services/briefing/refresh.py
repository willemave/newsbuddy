from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from math import ceil
from time import perf_counter
from typing import Literal

from sqlalchemy import func, or_, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.models.contracts import TaskQueue, TaskStatus, TaskType
from app.models.db import (
    BriefingLens,
    BriefingPendingSource,
    BriefingSegment,
    BriefingState,
    ProcessingTask,
)
from app.pipeline.task_specs import get_task_spec
from app.services.briefing import compaction, window_composition
from app.services.briefing.composer import generate_layout_with_llm, plan_windows
from app.services.briefing.eligibility import is_briefing_enabled_for_user
from app.services.briefing.lenses import (
    assign_pending_lenses,
    build_llm_lens_namer,
    ensure_base_lenses,
    retire_idle_lenses,
)
from app.services.briefing.openrouter import BriefingOpenRouterClient
from app.services.briefing.segments import build_briefing_segment
from app.services.briefing.sources import (
    BriefingSource,
    list_bootstrap_sources,
    read_source_keys,
    sources_for_keys,
)
from app.services.briefing.taxonomy import apply_taxonomy_if_needed

RefreshMode = Literal["append", "sweep", "full"]
ACTIVE_DEDUPE_WHERE = text("dedupe_key IS NOT NULL AND status IN ('pending', 'processing')")
logger = get_logger(__name__)


@dataclass(frozen=True)
class BriefingRefreshResult:
    user_id: int
    version: int
    appended_segments: int
    retired_segments: int
    compacted_segments: int
    pending_added: int
    sweep_enqueued: bool


@dataclass(frozen=True)
class _PreparedWindow:
    lens_id: int
    lens_key: str
    lens_title: str
    tier: str
    window_index: int
    pending_row_ids: tuple[int, ...]
    sources: tuple[BriefingSource, ...]


def ensure_state(db: Session, *, user_id: int, settings: Settings | None = None) -> BriefingState:
    settings = settings or get_settings()
    state = db.query(BriefingState).filter(BriefingState.user_id == user_id).first()
    if state is not None:
        return state
    state = BriefingState(
        user_id=user_id,
        version=0,
        masthead_title=settings.briefing_masthead_title,
        masthead_deck="A fresh edition will appear as unread sources arrive.",
    )
    db.add(state)
    db.flush()
    return state


def enqueue_ready_source(
    db: Session,
    *,
    user_id: int,
    source_kind: str,
    source_id: int,
    lens_key: str | None = None,
    delay_seconds: int | None = None,
    settings: Settings | None = None,
) -> bool:
    """Insert a pending source and debounce a refresh for an eligible user."""

    settings = settings or get_settings()
    inserted = _insert_pending_source(
        db,
        user_id=user_id,
        source_kind=source_kind,
        source_id=source_id,
        lens_key=lens_key,
    )
    enqueue_briefing_refresh_task(
        db,
        user_id=user_id,
        mode="append",
        delay_seconds=settings.briefing_debounce_seconds
        if delay_seconds is None
        else delay_seconds,
    )
    return inserted


def enqueue_briefing_refresh_task(
    db: Session,
    *,
    user_id: int,
    mode: RefreshMode,
    delay_seconds: int,
) -> bool:
    """Enqueue a delayed refresh task using the same active dedupe constraint as QueueService."""

    payload = get_task_spec(TaskType.BRIEFING_REFRESH).normalize_payload(
        {"user_id": user_id, "mode": mode}
    )
    available_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=max(delay_seconds, 0))
    dedupe_key = _refresh_dedupe_key(user_id=user_id, mode=mode)
    inserted = db.execute(
        postgresql_insert(ProcessingTask)
        .values(
            task_type=TaskType.BRIEFING_REFRESH.value,
            payload=payload,
            status=TaskStatus.PENDING.value,
            queue_name=TaskQueue.LLM.value,
            available_at=available_at,
            dedupe_key=dedupe_key,
        )
        .on_conflict_do_nothing(
            index_elements=[ProcessingTask.dedupe_key],
            index_where=ACTIVE_DEDUPE_WHERE,
        )
        .returning(ProcessingTask.id)
    ).scalar_one_or_none()
    if inserted is not None:
        return True
    updated = (
        db.query(ProcessingTask)
        .filter(ProcessingTask.dedupe_key == dedupe_key)
        .filter(ProcessingTask.status == TaskStatus.PENDING.value)
        .filter(
            or_(
                ProcessingTask.available_at.is_(None),
                ProcessingTask.available_at > available_at,
            )
        )
        .update({ProcessingTask.available_at: available_at}, synchronize_session=False)
    )
    return bool(updated)


def run_briefing_refresh(
    db: Session,
    *,
    user_id: int,
    mode: RefreshMode = "append",
    task_id: int | None = None,
    use_llm: bool = True,
    settings: Settings | None = None,
) -> BriefingRefreshResult:
    """Refresh one user's Briefing without holding a DB transaction during composition."""

    settings = settings or get_settings()
    state = ensure_state(db, user_id=user_id, settings=settings)
    version = int(state.version or 0)
    if not is_briefing_enabled_for_user(db, user_id=user_id, settings=settings):
        return BriefingRefreshResult(user_id, version, 0, 0, 0, 0, False)

    ensure_base_lenses(db, user_id=user_id)
    pending_added = _seed_pending_from_unread(
        db,
        user_id=user_id,
        mode=mode,
        settings=settings,
        compact_existing=mode != "full",
    )
    if pending_added:
        db.flush()
    db.commit()
    if mode == "append" and _pending_source_count(db, user_id=user_id) == 0:
        return _finish_empty_append_refresh(
            db,
            user_id=user_id,
            version=version,
            pending_added=pending_added,
            settings=settings,
        )
    taxonomy_model = settings.briefing_taxonomy_model or settings.briefing_model
    uses_openrouter = use_llm and (
        settings.briefing_model.startswith("openrouter:")
        or (settings.briefing_taxonomy_planner_enabled and taxonomy_model.startswith("openrouter:"))
    )
    provider_context = (
        BriefingOpenRouterClient(
            timeout_seconds=settings.briefing_llm_timeout_seconds,
            settings=settings,
        )
        if uses_openrouter
        else nullcontext(None)
    )
    with provider_context as openrouter_client:
        structured_output_requester = (
            openrouter_client.request_json_schema if openrouter_client is not None else None
        )
        naming_fn = (
            build_llm_lens_namer(
                settings=settings,
                task_id=task_id,
                user_id=user_id,
                structured_output_requester=structured_output_requester,
            )
            if use_llm
            else None
        )
        assigned = assign_pending_lenses(
            db,
            user_id=user_id,
            naming_fn=naming_fn,
            settings=settings,
        )
        if assigned:
            db.flush()
        taxonomized = apply_taxonomy_if_needed(
            db,
            user_id=user_id,
            settings=settings,
            task_id=task_id,
            use_llm=use_llm,
            structured_output_requester=structured_output_requester,
        )
        if taxonomized:
            db.flush()
        prepared_windows = _plan_ready_windows(db, user_id=user_id, mode=mode, settings=settings)
        reserved_segment_counts: dict[int, int] = {}
        for window in prepared_windows:
            reserved_segment_counts[window.lens_id] = (
                reserved_segment_counts.get(window.lens_id, 0) + 1
            )
        prepared_compactions = (
            []
            if mode == "full"
            else compaction.prepare_compactions(
                db,
                user_id=user_id,
                settings=settings,
                reserved_segment_counts=reserved_segment_counts,
            )
        )
        db.commit()

        compaction_windows = [window for plan in prepared_compactions for window in plan.windows]
        layout_generator = (
            partial(
                generate_layout_with_llm,
                structured_output_requester=structured_output_requester,
            )
            if structured_output_requester is not None
            else None
        )
        composition_started_at = perf_counter()
        composed_windows, composed_compactions = window_composition.compose_window_groups(
            prepared_windows,
            compaction_windows,
            user_id=user_id,
            task_id=task_id,
            use_llm=use_llm,
            settings=settings,
            layout_generator=layout_generator,
        )
        composition_ms = round((perf_counter() - composition_started_at) * 1000, 2)

    state = ensure_state(db, user_id=user_id, settings=settings)
    version = int(state.version or 0)
    if mode == "full":
        db.query(BriefingSegment).filter(BriefingSegment.user_id == user_id).filter(
            BriefingSegment.status.in_(("active", "degraded"))
        ).update({BriefingSegment.status: "compacted"}, synchronize_session=False)
    append_persistence_started_at = perf_counter()
    appended = _persist_composed_windows(db, user_id=user_id, composed_windows=composed_windows)
    append_persistence_ms = round((perf_counter() - append_persistence_started_at) * 1000, 2)
    compaction_persistence_started_at = perf_counter()
    compacted = compaction.persist_compactions(
        db,
        user_id=user_id,
        plans=prepared_compactions,
        composed_windows=composed_compactions,
    )
    compaction_persistence_ms = round(
        (perf_counter() - compaction_persistence_started_at) * 1000,
        2,
    )
    logger.info(
        "Briefing refresh composition and publication measured",
        extra={
            "component": "briefing",
            "operation": "measure_refresh_publication",
            "item_id": user_id,
            "task_id": task_id,
            "context_data": {
                "append_window_count": len(prepared_windows),
                "append_persistence_ms": append_persistence_ms,
                "compaction_window_count": len(composed_compactions),
                "compaction_persistence_ms": compaction_persistence_ms,
                "composition_ms": composition_ms,
            },
        },
    )
    retired = _retire_finished_segments(db, user_id=user_id, settings=settings)
    retired += retire_idle_lenses(db, user_id=user_id, idle_days=settings.briefing_lens_idle_days)
    state.last_sweep_at = datetime.now(UTC).replace(tzinfo=None)
    mutated = bool(pending_added or assigned or taxonomized or appended or retired or compacted)
    if appended:
        state.last_append_at = datetime.now(UTC).replace(tzinfo=None)
        state.masthead_deck = _masthead_deck(db, user_id=user_id)
    if mutated:
        version += 1
        state.version = version
    if task_id is not None and mode == "sweep":
        _release_current_sweep_dedupe(db, task_id=task_id)
    sweep_enqueued = _schedule_next_sweep(db, user_id=user_id, settings=settings)
    db.flush()
    return BriefingRefreshResult(
        user_id=user_id,
        version=version,
        appended_segments=appended,
        retired_segments=retired,
        compacted_segments=compacted,
        pending_added=pending_added,
        sweep_enqueued=sweep_enqueued,
    )


def _refresh_dedupe_key(*, user_id: int, mode: RefreshMode) -> str:
    return f"briefing_refresh:{user_id}:{mode}"


def _finish_empty_append_refresh(
    db: Session,
    *,
    user_id: int,
    version: int,
    pending_added: int,
    settings: Settings,
) -> BriefingRefreshResult:
    state = ensure_state(db, user_id=user_id, settings=settings)
    state.last_sweep_at = datetime.now(UTC).replace(tzinfo=None)
    sweep_enqueued = _schedule_next_sweep(db, user_id=user_id, settings=settings)
    db.flush()
    return BriefingRefreshResult(
        user_id=user_id,
        version=version,
        appended_segments=0,
        retired_segments=0,
        compacted_segments=0,
        pending_added=pending_added,
        sweep_enqueued=sweep_enqueued,
    )


def _pending_source_count(db: Session, *, user_id: int) -> int:
    return int(
        db.query(func.count(BriefingPendingSource.id))
        .filter(BriefingPendingSource.user_id == user_id)
        .scalar()
        or 0
    )


def _release_current_sweep_dedupe(db: Session, *, task_id: int) -> None:
    db.query(ProcessingTask).filter(ProcessingTask.id == task_id).filter(
        ProcessingTask.task_type == TaskType.BRIEFING_REFRESH.value,
        ProcessingTask.status == TaskStatus.PROCESSING.value,
    ).update({ProcessingTask.dedupe_key: None}, synchronize_session=False)


def _schedule_next_sweep(db: Session, *, user_id: int, settings: Settings) -> bool:
    pending_delay = _next_pending_news_deadline_delay(db, user_id=user_id, settings=settings)
    delay_seconds = settings.briefing_sweep_seconds
    if pending_delay is not None:
        delay_seconds = min(delay_seconds, pending_delay)
    return enqueue_briefing_refresh_task(
        db,
        user_id=user_id,
        mode="sweep",
        delay_seconds=delay_seconds,
    )


def _next_pending_news_deadline_delay(
    db: Session,
    *,
    user_id: int,
    settings: Settings,
) -> int | None:
    rows = (
        db.query(BriefingPendingSource)
        .outerjoin(
            BriefingLens,
            (BriefingLens.user_id == BriefingPendingSource.user_id)
            & (BriefingLens.key == BriefingPendingSource.lens_key),
        )
        .filter(
            BriefingPendingSource.user_id == user_id,
            BriefingPendingSource.source_kind == "news",
            or_(
                BriefingPendingSource.lens_key.is_(None),
                (BriefingLens.status == "active") & (BriefingLens.tier == "news"),
            ),
        )
        .order_by(BriefingPendingSource.enqueued_at.asc(), BriefingPendingSource.id.asc())
        .all()
    )
    if not rows:
        return None

    oldest_by_lens: dict[str, datetime] = {}
    counts_by_lens: dict[str, int] = {}
    for row in rows:
        lens_key = row.lens_key or "__unassigned__"
        enqueued_at = row.enqueued_at
        if not isinstance(enqueued_at, datetime):
            continue
        counts_by_lens[lens_key] = counts_by_lens.get(lens_key, 0) + 1
        oldest_by_lens.setdefault(lens_key, enqueued_at)

    now = datetime.now(UTC).replace(tzinfo=None)
    delays = [
        max(
            0,
            ceil(
                (
                    oldest + timedelta(seconds=settings.briefing_pending_max_age_seconds) - now
                ).total_seconds()
            ),
        )
        for lens_key, oldest in oldest_by_lens.items()
        if counts_by_lens.get(lens_key, 0) < settings.briefing_window_min
    ]
    return min(delays) if delays else 0


def _pending_rows_are_ready(
    pending_rows: list[BriefingPendingSource],
    *,
    tier: str,
    mode: RefreshMode,
    settings: Settings,
    now: datetime,
) -> bool:
    if tier != "news" or mode == "full" or len(pending_rows) >= settings.briefing_window_min:
        return True
    oldest = pending_rows[0].enqueued_at
    if not isinstance(oldest, datetime):
        return False
    return (now - oldest).total_seconds() >= settings.briefing_pending_max_age_seconds


def _seed_pending_from_unread(
    db: Session,
    *,
    user_id: int,
    mode: RefreshMode,
    settings: Settings,
    compact_existing: bool = True,
) -> int:
    if mode == "full":
        db.query(BriefingPendingSource).filter(BriefingPendingSource.user_id == user_id).delete()
        if compact_existing:
            db.query(BriefingSegment).filter(BriefingSegment.user_id == user_id).filter(
                BriefingSegment.status.in_(("active", "degraded"))
            ).update({BriefingSegment.status: "compacted"}, synchronize_session=False)

    covered = (
        set()
        if mode == "full" and not compact_existing
        else _covered_source_keys(db, user_id=user_id)
    )
    sources = list_bootstrap_sources(
        db,
        user_id=user_id,
        audio_limit=None,
        longform_limit=None,
        news_limit=None,
    )
    added = 0
    for source in sources:
        if source.source_key in covered:
            continue
        added += int(
            _insert_pending_source(
                db,
                user_id=user_id,
                source_kind=source.kind,
                source_id=source.id,
                lens_key=source.lens_key,
            )
        )
    return added


def _plan_ready_windows(
    db: Session,
    *,
    user_id: int,
    mode: RefreshMode,
    settings: Settings,
) -> list[_PreparedWindow]:
    windows: list[_PreparedWindow] = []
    lenses = (
        db.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id, BriefingLens.status == "active")
        .order_by(BriefingLens.position.asc(), BriefingLens.id.asc())
        .all()
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    pending_by_lens_key: dict[str, list[BriefingPendingSource]] = {}
    if lenses:
        pending_rows = (
            db.query(BriefingPendingSource)
            .filter(BriefingPendingSource.user_id == user_id)
            .filter(BriefingPendingSource.lens_key.in_([str(lens.key) for lens in lenses]))
            .order_by(
                BriefingPendingSource.lens_key.asc(),
                BriefingPendingSource.enqueued_at.asc(),
                BriefingPendingSource.id.asc(),
            )
            .all()
        )
        for row in pending_rows:
            if row.lens_key is not None:
                pending_by_lens_key.setdefault(str(row.lens_key), []).append(row)

    ready_lenses: list[tuple[BriefingLens, list[BriefingPendingSource], list[str]]] = []
    source_keys: list[str] = []
    for lens in lenses:
        if lens.id is None:
            continue
        pending_rows = pending_by_lens_key.get(str(lens.key), [])
        if not pending_rows:
            continue
        if not _pending_rows_are_ready(
            pending_rows,
            tier=str(lens.tier),
            mode=mode,
            settings=settings,
            now=now,
        ):
            continue
        lens_source_keys = [f"{row.source_kind}:{row.source_id}" for row in pending_rows]
        ready_lenses.append((lens, pending_rows, lens_source_keys))
        source_keys.extend(lens_source_keys)

    source_map = sources_for_keys(
        db,
        user_id=user_id,
        source_keys=list(dict.fromkeys(source_keys)),
    )
    for lens, pending_rows, lens_source_keys in ready_lenses:
        assert lens.id is not None
        for row, key in zip(pending_rows, lens_source_keys, strict=True):
            if key not in source_map:
                db.delete(row)
        source_rows = [
            (int(row.id), source_map[key])
            for row, key in zip(pending_rows, lens_source_keys, strict=True)
            if row.id is not None and key in source_map
        ]
        if not source_rows:
            continue
        for window_index, window_rows in enumerate(
            plan_windows(source_rows, tier=str(lens.tier), settings=settings),
            start=1,
        ):
            windows.append(
                _PreparedWindow(
                    lens_id=int(lens.id),
                    lens_key=str(lens.key),
                    lens_title=str(lens.title),
                    tier=str(lens.tier),
                    window_index=window_index,
                    pending_row_ids=tuple(row_id for row_id, _source in window_rows),
                    sources=tuple(source for _row_id, source in window_rows),
                )
            )
    return windows


def _persist_composed_windows(
    db: Session,
    *,
    user_id: int,
    composed_windows: list[window_composition.ComposedWindow[_PreparedWindow]],
) -> int:
    pending_row_ids: list[int] = []
    for composed in composed_windows:
        prepared = composed.prepared
        segment = composed.segment
        db.add(
            build_briefing_segment(
                lens_id=prepared.lens_id,
                user_id=user_id,
                segment=segment,
                source_keys=[source.source_key for source in prepared.sources],
            )
        )
        pending_row_ids.extend(prepared.pending_row_ids)
    if pending_row_ids:
        db.query(BriefingPendingSource).filter(
            BriefingPendingSource.id.in_(pending_row_ids)
        ).delete(synchronize_session=False)
    return len(composed_windows)


def _retire_finished_segments(db: Session, *, user_id: int, settings: Settings) -> int:
    read_keys = read_source_keys(db, user_id=user_id)
    active = (
        db.query(BriefingSegment)
        .filter(BriefingSegment.user_id == user_id)
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .all()
    )
    retired = 0
    for segment in active:
        source_keys = [str(key) for key in (segment.source_keys or [])]
        if source_keys and set(source_keys).issubset(read_keys):
            segment.status = "retired"
            retired += 1
    return retired


def _insert_pending_source(
    db: Session,
    *,
    user_id: int,
    source_kind: str,
    source_id: int,
    lens_key: str | None,
) -> bool:
    existing = (
        db.query(BriefingPendingSource)
        .filter(
            BriefingPendingSource.user_id == user_id,
            BriefingPendingSource.source_kind == source_kind,
            BriefingPendingSource.source_id == source_id,
        )
        .first()
    )
    if existing is not None:
        if existing.lens_key is None and lens_key is not None:
            existing.lens_key = lens_key
        return False
    db.add(
        BriefingPendingSource(
            user_id=user_id,
            lens_key=lens_key,
            source_kind=source_kind,
            source_id=source_id,
        )
    )
    return True


def _covered_source_keys(db: Session, *, user_id: int) -> set[str]:
    segments = (
        db.query(BriefingSegment.source_keys)
        .filter(BriefingSegment.user_id == user_id)
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .all()
    )
    return {str(key) for (keys,) in segments for key in (keys or [])}


def _masthead_deck(db: Session, *, user_id: int) -> str:
    newest_titles = [
        title
        for (title,) in db.query(BriefingLens.title)
        .filter(BriefingLens.user_id == user_id, BriefingLens.status == "active")
        .order_by(BriefingLens.position.asc())
        .limit(4)
        .all()
    ]
    if not newest_titles:
        return "A fresh edition will appear as unread sources arrive."
    return "New unread segments are ready across " + ", ".join(newest_titles) + "."


def status_counts(db: Session, *, user_id: int) -> dict[str, int]:
    """Return a compact status rollup for admin and tests."""

    return {
        "lenses": int(
            db.query(func.count(BriefingLens.id)).filter(BriefingLens.user_id == user_id).scalar()
            or 0
        ),
        "segments": int(
            db.query(func.count(BriefingSegment.id))
            .filter(BriefingSegment.user_id == user_id)
            .scalar()
            or 0
        ),
        "pending": int(
            db.query(func.count(BriefingPendingSource.id))
            .filter(BriefingPendingSource.user_id == user_id)
            .scalar()
            or 0
        ),
        "degraded": int(
            db.query(func.count(BriefingSegment.id))
            .filter(BriefingSegment.user_id == user_id, BriefingSegment.status == "degraded")
            .scalar()
            or 0
        ),
    }
