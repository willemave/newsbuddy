from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, text
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
from app.services.briefing.composer import ComposedSegment, compose_window, plan_windows
from app.services.briefing.lenses import (
    assign_pending_lenses,
    build_llm_lens_namer,
    ensure_base_lenses,
    retire_idle_lenses,
)
from app.services.briefing.sources import (
    BriefingSource,
    list_bootstrap_sources,
    read_source_keys,
    sources_for_keys,
)
from app.services.briefing.taxonomy import apply_taxonomy_if_needed

RefreshMode = Literal["append", "sweep", "full"]
COMPACTION_WINDOW_INDEX = 99
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


@dataclass(frozen=True)
class _ComposedWindow:
    prepared: _PreparedWindow
    segment: ComposedSegment


def is_briefing_enabled_for_user(user_id: int, *, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return int(user_id) in {int(value) for value in settings.briefing_enabled_user_ids}


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
    """Insert a pending source and debounce a briefing refresh for enabled users."""

    settings = settings or get_settings()
    if not is_briefing_enabled_for_user(user_id, settings=settings):
        return False
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
    if delay_seconds > 0:
        return False
    updated = (
        db.query(ProcessingTask)
        .filter(ProcessingTask.dedupe_key == dedupe_key)
        .filter(ProcessingTask.status == TaskStatus.PENDING.value)
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
    release_db_during_compose: bool = False,
    settings: Settings | None = None,
) -> BriefingRefreshResult:
    settings = settings or get_settings()
    if release_db_during_compose:
        return _run_refresh_releasing_db(
            db,
            user_id=user_id,
            mode=mode,
            task_id=task_id,
            use_llm=use_llm,
            settings=settings,
        )

    state = ensure_state(db, user_id=user_id, settings=settings)
    version = int(state.version or 0)
    if not is_briefing_enabled_for_user(user_id, settings=settings):
        return BriefingRefreshResult(user_id, version, 0, 0, 0, 0, False)

    ensure_base_lenses(db, user_id=user_id)
    pending_added = _seed_pending_from_unread(db, user_id=user_id, mode=mode, settings=settings)
    if pending_added:
        db.flush()
    if mode == "append" and _pending_source_count(db, user_id=user_id) == 0:
        return _finish_empty_append_refresh(
            db,
            user_id=user_id,
            version=version,
            pending_added=pending_added,
            settings=settings,
        )
    naming_fn = (
        build_llm_lens_namer(settings=settings, task_id=task_id, user_id=user_id)
        if use_llm
        else None
    )
    assigned = assign_pending_lenses(db, user_id=user_id, naming_fn=naming_fn, settings=settings)
    if assigned:
        db.flush()
    taxonomized = apply_taxonomy_if_needed(
        db,
        user_id=user_id,
        settings=settings,
        task_id=task_id,
        use_llm=use_llm,
    )
    if taxonomized:
        db.flush()
    appended = _append_ready_windows(
        db,
        user_id=user_id,
        mode=mode,
        task_id=task_id,
        use_llm=use_llm,
        settings=settings,
    )
    retired = _retire_finished_segments(db, user_id=user_id, settings=settings)
    compacted = _compact_fragmented_lenses(
        db,
        user_id=user_id,
        task_id=task_id,
        use_llm=use_llm,
        settings=settings,
    )
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
    sweep_enqueued = enqueue_briefing_refresh_task(
        db,
        user_id=user_id,
        mode="sweep",
        delay_seconds=settings.briefing_sweep_seconds,
    )
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


def _run_refresh_releasing_db(
    db: Session,
    *,
    user_id: int,
    mode: RefreshMode,
    task_id: int | None,
    use_llm: bool,
    settings: Settings,
) -> BriefingRefreshResult:
    state = ensure_state(db, user_id=user_id, settings=settings)
    version = int(state.version or 0)
    if not is_briefing_enabled_for_user(user_id, settings=settings):
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

    naming_fn = (
        build_llm_lens_namer(settings=settings, task_id=task_id, user_id=user_id)
        if use_llm
        else None
    )
    assigned = assign_pending_lenses(db, user_id=user_id, naming_fn=naming_fn, settings=settings)
    if assigned:
        db.flush()
    taxonomized = apply_taxonomy_if_needed(
        db,
        user_id=user_id,
        settings=settings,
        task_id=task_id,
        use_llm=use_llm,
    )
    if taxonomized:
        db.flush()
    prepared_windows = _plan_ready_windows(db, user_id=user_id, mode=mode, settings=settings)
    db.commit()

    composed_windows = _compose_prepared_windows(
        prepared_windows,
        user_id=user_id,
        task_id=task_id,
        use_llm=use_llm,
        settings=settings,
    )

    state = ensure_state(db, user_id=user_id, settings=settings)
    version = int(state.version or 0)
    if mode == "full":
        db.query(BriefingSegment).filter(BriefingSegment.user_id == user_id).filter(
            BriefingSegment.status.in_(("active", "degraded"))
        ).update({BriefingSegment.status: "compacted"}, synchronize_session=False)
    appended = _persist_composed_windows(db, user_id=user_id, composed_windows=composed_windows)
    retired = _retire_finished_segments(db, user_id=user_id, settings=settings)
    compacted = 0
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
    sweep_enqueued = enqueue_briefing_refresh_task(
        db,
        user_id=user_id,
        mode="sweep",
        delay_seconds=settings.briefing_sweep_seconds,
    )
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
    sweep_enqueued = enqueue_briefing_refresh_task(
        db,
        user_id=user_id,
        mode="sweep",
        delay_seconds=settings.briefing_sweep_seconds,
    )
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
    for lens in lenses:
        if lens.id is None:
            continue
        pending_rows = (
            db.query(BriefingPendingSource)
            .filter(BriefingPendingSource.user_id == user_id)
            .filter(BriefingPendingSource.lens_key == lens.key)
            .order_by(BriefingPendingSource.enqueued_at.asc(), BriefingPendingSource.id.asc())
            .all()
        )
        if not pending_rows:
            continue
        source_keys = [f"{row.source_kind}:{row.source_id}" for row in pending_rows]
        source_map = sources_for_keys(db, user_id=user_id, source_keys=source_keys)
        for row, key in zip(pending_rows, source_keys, strict=True):
            if key not in source_map:
                db.delete(row)
        source_rows = [
            (int(row.id), source_map[key])
            for row, key in zip(pending_rows, source_keys, strict=True)
            if row.id is not None and key in source_map
        ]
        if not source_rows:
            continue
        max_size = (
            settings.briefing_news_window_max
            if str(lens.tier) == "news"
            else settings.briefing_window_max
        )
        max_size = max(1, max_size)
        for window_index, start in enumerate(range(0, len(source_rows), max_size), start=1):
            window_rows = source_rows[start : start + max_size]
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


def _compose_prepared_windows(
    prepared_windows: list[_PreparedWindow],
    *,
    user_id: int,
    task_id: int | None,
    use_llm: bool,
    settings: Settings,
) -> list[_ComposedWindow]:
    def compose_one(window: _PreparedWindow) -> _ComposedWindow:
        segment = compose_window(
            list(window.sources),
            lens_key=window.lens_key,
            lens_title=window.lens_title,
            tier=window.tier,
            window_index=window.window_index,
            task_id=task_id,
            user_id=user_id,
            use_llm=use_llm,
            settings=settings,
        )
        return _ComposedWindow(prepared=window, segment=segment)

    max_workers = min(max(settings.briefing_compose_parallelism, 1), len(prepared_windows))
    if max_workers <= 1:
        return [compose_one(window) for window in prepared_windows]

    composed: list[_ComposedWindow] = []
    for start in range(0, len(prepared_windows), max_workers):
        batch = prepared_windows[start : start + max_workers]
        logger.info(
            "Briefing composition batch started",
            extra={
                "component": "briefing",
                "operation": "compose_batch",
                "item_id": user_id,
                "task_id": task_id,
                "context_data": {
                    "batch_start": start,
                    "batch_size": len(batch),
                    "window_count": len(prepared_windows),
                },
            },
        )
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = {executor.submit(compose_one, window): window for window in batch}
            batch_results: list[_ComposedWindow] = []
            for future in as_completed(futures):
                batch_results.append(future.result())
            result_by_key = {
                (result.prepared.lens_id, result.prepared.window_index): result
                for result in batch_results
            }
            composed.extend(
                result_by_key[(window.lens_id, window.window_index)] for window in batch
            )
            logger.info(
                "Briefing composition batch completed",
                extra={
                    "component": "briefing",
                    "operation": "compose_batch",
                    "item_id": user_id,
                    "task_id": task_id,
                    "context_data": {
                        "batch_start": start,
                        "batch_size": len(batch),
                        "window_count": len(prepared_windows),
                    },
                },
            )
    return composed


def _persist_composed_windows(
    db: Session,
    *,
    user_id: int,
    composed_windows: list[_ComposedWindow],
) -> int:
    pending_row_ids: list[int] = []
    for composed in composed_windows:
        prepared = composed.prepared
        segment = composed.segment
        db.add(
            BriefingSegment(
                lens_id=prepared.lens_id,
                user_id=user_id,
                blocks=segment.blocks,
                markdown_raw=segment.markdown_raw,
                narration_text=segment.narration_text,
                source_keys=[source.source_key for source in prepared.sources],
                status=segment.status,
                model=segment.model[:64],
                prompt_version=segment.prompt_version,
                input_tokens=segment.input_tokens,
                output_tokens=segment.output_tokens,
                generation_ms=segment.generation_ms,
                warnings=segment.warnings,
            )
        )
        pending_row_ids.extend(prepared.pending_row_ids)
    if pending_row_ids:
        db.query(BriefingPendingSource).filter(
            BriefingPendingSource.id.in_(pending_row_ids)
        ).delete(synchronize_session=False)
    return len(composed_windows)


def _append_ready_windows(
    db: Session,
    *,
    user_id: int,
    mode: RefreshMode,
    task_id: int | None,
    use_llm: bool,
    settings: Settings,
) -> int:
    appended = 0
    lenses = (
        db.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id, BriefingLens.status == "active")
        .order_by(BriefingLens.position.asc(), BriefingLens.id.asc())
        .all()
    )
    for lens in lenses:
        pending_rows = (
            db.query(BriefingPendingSource)
            .filter(BriefingPendingSource.user_id == user_id)
            .filter(BriefingPendingSource.lens_key == lens.key)
            .order_by(BriefingPendingSource.enqueued_at.asc(), BriefingPendingSource.id.asc())
            .all()
        )
        if not pending_rows:
            continue
        source_keys = [f"{row.source_kind}:{row.source_id}" for row in pending_rows]
        source_map = sources_for_keys(db, user_id=user_id, source_keys=source_keys)
        sources = [source_map[key] for key in source_keys if key in source_map]
        if not sources:
            for row in pending_rows:
                db.delete(row)
            continue
        for window_index, window in enumerate(
            plan_windows(sources, tier=str(lens.tier), settings=settings), start=1
        ):
            _compose_and_persist_segment(
                db,
                lens=lens,
                user_id=user_id,
                window=window,
                window_index=window_index,
                task_id=task_id,
                use_llm=use_llm,
                settings=settings,
            )
            appended += 1
        for row in pending_rows:
            db.delete(row)
    return appended


def _compose_and_persist_segment(
    db: Session,
    *,
    lens: BriefingLens,
    user_id: int,
    window: list[BriefingSource],
    window_index: int,
    task_id: int | None,
    use_llm: bool,
    settings: Settings,
    extra_warnings: tuple[str, ...] = (),
) -> None:
    if lens.id is None:
        raise ValueError("Cannot compose a briefing segment for an unpersisted lens")
    lens_id = int(lens.id)
    segment = compose_window(
        window,
        lens_key=str(lens.key),
        lens_title=str(lens.title),
        tier=str(lens.tier),
        window_index=window_index,
        task_id=task_id,
        user_id=user_id,
        use_llm=use_llm,
        settings=settings,
    )
    db.add(
        BriefingSegment(
            lens_id=lens_id,
            user_id=user_id,
            blocks=segment.blocks,
            markdown_raw=segment.markdown_raw,
            narration_text=segment.narration_text,
            source_keys=[source.source_key for source in window],
            status=segment.status,
            model=segment.model[:64],
            prompt_version=segment.prompt_version,
            input_tokens=segment.input_tokens,
            output_tokens=segment.output_tokens,
            generation_ms=segment.generation_ms,
            warnings=[*segment.warnings, *extra_warnings],
        )
    )


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


def _compact_fragmented_lenses(
    db: Session,
    *,
    user_id: int,
    task_id: int | None,
    use_llm: bool,
    settings: Settings,
) -> int:
    read_keys = read_source_keys(db, user_id=user_id)
    compacted = 0
    lenses = db.query(BriefingLens).filter(BriefingLens.user_id == user_id).all()
    for lens in lenses:
        segments = (
            db.query(BriefingSegment)
            .filter(BriefingSegment.lens_id == lens.id)
            .filter(BriefingSegment.status.in_(("active", "degraded")))
            .order_by(BriefingSegment.created_at.desc(), BriefingSegment.id.desc())
            .all()
        )
        if len(segments) <= settings.briefing_max_segments_per_lens:
            small = [
                segment
                for segment in segments
                if 0 < len(set(segment.source_keys or []) - read_keys) <= 2
            ]
            if len(small) < 3:
                continue
            donors = small
        else:
            donors = segments[settings.briefing_max_segments_per_lens - 1 :]
        source_keys = sorted(
            {key for donor in donors for key in (donor.source_keys or [])} - read_keys
        )
        if not source_keys:
            for donor in donors:
                donor.status = "retired"
                compacted += 1
            continue
        source_map = sources_for_keys(db, user_id=user_id, source_keys=source_keys)
        sources = [source_map[key] for key in source_keys if key in source_map]
        if len(sources) < settings.briefing_window_min:
            continue
        _compose_and_persist_segment(
            db,
            lens=lens,
            user_id=user_id,
            window=sources[: settings.briefing_window_max],
            window_index=COMPACTION_WINDOW_INDEX,
            task_id=task_id,
            use_llm=use_llm,
            settings=settings,
            extra_warnings=("compaction_segment",),
        )
        for donor in donors:
            donor.status = "compacted"
            compacted += 1
    return compacted


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
