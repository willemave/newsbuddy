from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from math import ceil
from time import perf_counter
from typing import Literal

from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.models.contracts import TaskStatus, TaskType
from app.models.db import (
    BriefingLens,
    BriefingPendingSource,
    BriefingSegment,
    ProcessingTask,
)
from app.services.briefing import compaction, window_composition
from app.services.briefing.composer import (
    LayoutGenerator,
    generate_layout_with_llm,
    plan_event_windows,
)
from app.services.briefing.eligibility import is_briefing_enabled_for_user
from app.services.briefing.first_run import bump_first_edition_revision
from app.services.briefing.lenses import (
    assign_pending_lenses,
    build_llm_lens_namer,
    ensure_base_lenses,
    retire_idle_lenses,
)
from app.services.briefing.openrouter import BriefingOpenRouterClient
from app.services.briefing.publication import (
    AppendWindow,
    PublicationPlan,
    publish_composed_plan,
)
from app.services.briefing.sources import (
    BriefingSource,
    eligible_unread_sources_for_keys,
    list_bootstrap_sources,
    read_source_keys,
)
from app.services.briefing.state import ensure_briefing_state
from app.services.briefing.taxonomy import apply_taxonomy_if_needed
from app.services.gateways.task_queue_gateway import get_task_queue_gateway
from app.services.queue import TaskEnqueueRequest

RefreshMode = Literal["append", "sweep", "full"]
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
    resolved_delay = delay_seconds
    if resolved_delay is None:
        if inserted:
            db.flush()
        resolved_delay = settings.briefing_debounce_seconds
        batch_minimum = _briefing_batch_minimum(settings=settings, lens_key=lens_key)
        if _pending_batch_source_count(db, user_id=user_id, lens_key=lens_key) >= batch_minimum:
            resolved_delay = 0
    enqueue_briefing_refresh_task(
        db,
        user_id=user_id,
        mode="append",
        delay_seconds=resolved_delay,
    )
    return inserted


def insert_pending_sources(
    db: Session,
    *,
    user_ids: set[int],
    source_kind: str,
    source_id: int,
    lens_key: str | None,
) -> set[int]:
    """Insert one ready source for many users and return newly inserted owners."""
    if not user_ids:
        return set()
    rows = [
        {
            "user_id": user_id,
            "lens_key": lens_key,
            "source_kind": source_kind,
            "source_id": source_id,
        }
        for user_id in sorted(user_ids)
    ]
    result = db.execute(
        postgresql_insert(BriefingPendingSource)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=[
                BriefingPendingSource.user_id,
                BriefingPendingSource.source_kind,
                BriefingPendingSource.source_id,
            ]
        )
        .returning(BriefingPendingSource.user_id)
    )
    inserted_user_ids = {
        int(user_id) for user_id in result.scalars().all() if isinstance(user_id, int)
    }
    if lens_key is not None:
        (
            db.query(BriefingPendingSource)
            .filter(
                BriefingPendingSource.user_id.in_(user_ids),
                BriefingPendingSource.source_kind == source_kind,
                BriefingPendingSource.source_id == source_id,
                BriefingPendingSource.lens_key.is_(None),
            )
            .update(
                {BriefingPendingSource.lens_key: lens_key},
                synchronize_session=False,
            )
        )
    return inserted_user_ids


def build_ready_source_refresh_requests(
    db: Session,
    *,
    user_ids: set[int],
    lens_key: str | None,
    settings: Settings,
) -> list[TaskEnqueueRequest]:
    """Build per-user refresh requests from the canonical batch threshold."""
    if not user_ids:
        return []
    pending_counts = {
        int(user_id): int(count)
        for user_id, count in db.query(
            BriefingPendingSource.user_id,
            func.count(BriefingPendingSource.id),
        )
        .filter(
            BriefingPendingSource.user_id.in_(user_ids),
            (
                BriefingPendingSource.lens_key.is_(None)
                if lens_key is None
                else BriefingPendingSource.lens_key == lens_key
            ),
        )
        .group_by(BriefingPendingSource.user_id)
        .all()
    }
    now = datetime.now(UTC).replace(tzinfo=None)
    batch_minimum = _briefing_batch_minimum(settings=settings, lens_key=lens_key)
    return [
        build_briefing_refresh_request(
            user_id=user_id,
            mode="append",
            available_at=now
            + timedelta(
                seconds=(
                    0
                    if pending_counts.get(user_id, 0) >= batch_minimum
                    else settings.briefing_debounce_seconds
                )
            ),
        )
        for user_id in sorted(user_ids)
    ]


def expedite_pending_refreshes(
    db: Session,
    *,
    requests: list[TaskEnqueueRequest],
    task_ids: list[int],
) -> None:
    """Pull existing pending refreshes forward to their newly calculated deadlines."""
    by_available_at: dict[datetime, list[int]] = {}
    for request, task_id in zip(requests, task_ids, strict=True):
        if request.available_at is not None:
            by_available_at.setdefault(request.available_at, []).append(task_id)
    for available_at, ids in by_available_at.items():
        (
            db.query(ProcessingTask)
            .filter(
                ProcessingTask.id.in_(ids),
                ProcessingTask.status == TaskStatus.PENDING.value,
                or_(
                    ProcessingTask.available_at.is_(None),
                    ProcessingTask.available_at > available_at,
                ),
            )
            .update(
                {ProcessingTask.available_at: available_at},
                synchronize_session=False,
            )
        )


def enqueue_briefing_refresh_task(
    db: Session,
    *,
    user_id: int,
    mode: RefreshMode,
    delay_seconds: int,
) -> bool:
    """Enqueue a delayed refresh task using the same active dedupe constraint as QueueService."""

    available_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=max(delay_seconds, 0))
    request = build_briefing_refresh_request(
        user_id=user_id,
        mode=mode,
        available_at=available_at,
    )
    dedupe_key = str(request.dedupe_key)
    existing_task_id = (
        db.query(ProcessingTask.id)
        .filter(ProcessingTask.dedupe_key == dedupe_key)
        .filter(ProcessingTask.status.in_((TaskStatus.PENDING.value, TaskStatus.PROCESSING.value)))
        .scalar()
    )
    task_id = get_task_queue_gateway().enqueue_many_in_session(
        db,
        [request],
    )[0]
    if existing_task_id is None:
        return True
    updated = (
        db.query(ProcessingTask)
        .filter(ProcessingTask.id == task_id)
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


def build_briefing_refresh_request(
    *,
    user_id: int,
    mode: RefreshMode,
    available_at: datetime,
) -> TaskEnqueueRequest:
    """Build the canonical refresh request for atomic multi-user fanout."""
    return TaskEnqueueRequest(
        task_type=TaskType.BRIEFING_REFRESH,
        payload={"user_id": user_id, "mode": mode},
        dedupe_key=_refresh_dedupe_key(user_id=user_id, mode=mode),
        owner_user_id=user_id,
        available_at=available_at,
    )


def run_briefing_refresh(
    db: Session,
    *,
    user_id: int,
    mode: RefreshMode = "append",
    task_id: int | None = None,
    use_llm: bool = True,
    settings: Settings | None = None,
    layout_generator: LayoutGenerator | None = None,
) -> BriefingRefreshResult:
    """Refresh one user's Briefing without holding a DB transaction during composition."""

    settings = settings or get_settings()
    state = ensure_briefing_state(db, user_id=user_id, settings=settings)
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
        planning_version = int(
            ensure_briefing_state(db, user_id=user_id, settings=settings).version or 0
        )
        prepared_windows = _plan_ready_windows(db, user_id=user_id, mode=mode, settings=settings)
        prepared_compactions = (
            []
            if mode == "full"
            else compaction.prepare_compactions(
                db,
                user_id=user_id,
                settings=settings,
            )
        )
        publication_plan = PublicationPlan(
            starting_version=planning_version,
            append_windows=tuple(prepared_windows),
            compaction_plans=tuple(prepared_compactions),
        )
        first_run_progress_changed = mode != "full" and bool(
            pending_added or assigned or taxonomized or prepared_windows
        )
        if first_run_progress_changed:
            bump_first_edition_revision(db, user_id=user_id)
        db.commit()

        compaction_windows = [window for plan in prepared_compactions for window in plan.windows]
        resolved_layout_generator = layout_generator or (
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
            settings=settings,
            layout_generator=resolved_layout_generator,
        )
        composition_ms = round((perf_counter() - composition_started_at) * 1000, 2)

    publication_started_at = perf_counter()
    publication = publish_composed_plan(
        db,
        user_id=user_id,
        plan=publication_plan,
        composed_append_windows=composed_windows,
        composed_compaction_windows=composed_compactions,
        replace_existing=mode == "full",
        settings=settings,
    )
    publication_ms = round((perf_counter() - publication_started_at) * 1000, 2)
    state = publication.state
    version = int(state.version or 0)
    appended = publication.appended_segments
    compacted = publication.compacted_segments
    logger.info(
        "Briefing refresh composition and publication measured",
        extra={
            "component": "briefing",
            "operation": "measure_refresh_publication",
            "item_id": user_id,
            "task_id": task_id,
            "context_data": {
                "append_window_count": len(prepared_windows),
                "compaction_window_count": len(composed_compactions),
                "composition_ms": composition_ms,
                "publication_ms": publication_ms,
                "publication_stale": publication.stale,
                "append_publication_stale": publication.append_stale,
            },
        },
    )
    retired = _retire_finished_segments(db, user_id=user_id, settings=settings)
    retired += retire_idle_lenses(db, user_id=user_id, idle_days=settings.briefing_lens_idle_days)
    state.last_sweep_at = datetime.now(UTC).replace(tzinfo=None)
    mutated = bool(appended or retired or compacted or assigned or taxonomized)
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
    pending_added: int,
    settings: Settings,
) -> BriefingRefreshResult:
    state = ensure_briefing_state(db, user_id=user_id, settings=settings)
    state.last_sweep_at = datetime.now(UTC).replace(tzinfo=None)
    sweep_enqueued = _schedule_next_sweep(db, user_id=user_id, settings=settings)
    db.flush()
    return BriefingRefreshResult(
        user_id=user_id,
        version=int(state.version or 0),
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


def _pending_batch_source_count(
    db: Session,
    *,
    user_id: int,
    lens_key: str | None,
) -> int:
    query = db.query(func.count(BriefingPendingSource.id)).filter(
        BriefingPendingSource.user_id == user_id
    )
    if lens_key is None:
        query = query.filter(BriefingPendingSource.lens_key.is_(None))
    else:
        query = query.filter(BriefingPendingSource.lens_key == lens_key)
    return int(query.scalar() or 0)


def _briefing_batch_minimum(*, settings: Settings, lens_key: str | None) -> int:
    minimum = settings.briefing_window_min
    if lens_key is None:
        minimum = max(minimum, settings.briefing_new_lens_min_items)
    return minimum


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
) -> list[AppendWindow]:
    windows: list[AppendWindow] = []
    lenses = (
        db.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id, BriefingLens.status == "active")
        .order_by(BriefingLens.position.asc(), BriefingLens.id.asc())
        .all()
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    pending_by_lens_key: dict[
        str,
        list[tuple[BriefingPendingSource, BriefingSource]],
    ] = {}
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
        pending_with_keys = [(row, f"{row.source_kind}:{row.source_id}") for row in pending_rows]
        source_keys = list(dict.fromkeys(key for _row, key in pending_with_keys))
        source_map = eligible_unread_sources_for_keys(
            db,
            user_id=user_id,
            source_keys=source_keys,
        )
        for row, key in pending_with_keys:
            source = source_map.get(key)
            if source is None:
                db.delete(row)
                continue
            if row.lens_key is not None:
                pending_by_lens_key.setdefault(str(row.lens_key), []).append((row, source))

    for lens in lenses:
        if lens.id is None:
            continue
        pending_sources = pending_by_lens_key.get(str(lens.key), [])
        if not pending_sources:
            continue
        pending_rows = [row for row, _source in pending_sources]
        if not _pending_rows_are_ready(
            pending_rows,
            tier=str(lens.tier),
            mode=mode,
            settings=settings,
            now=now,
        ):
            continue
        source_rows = [
            (int(row.id), source) for row, source in pending_sources if row.id is not None
        ]
        planned_windows = plan_event_windows(
            source_rows,
            tier=str(lens.tier),
            settings=settings,
            source_of=lambda row: row[1],
        )
        if mode != "full":
            planned_windows = planned_windows[:1]
        for window_index, window_events in enumerate(planned_windows, start=1):
            window_rows = [row for event in window_events for row in event]
            windows.append(
                AppendWindow(
                    lens_id=int(lens.id),
                    lens_key=str(lens.key),
                    lens_title=str(lens.title),
                    tier=str(lens.tier),
                    window_index=window_index,
                    pending_row_ids=tuple(row_id for row_id, _source in window_rows),
                    sources=tuple(source for _row_id, source in window_rows),
                    event_groups=tuple(
                        tuple(source.source_key for _row_id, source in event)
                        for event in window_events
                    ),
                )
            )
    return windows


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
