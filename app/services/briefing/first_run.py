"""Durable progress for the temporary Start Here briefing page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.api.briefing import BriefingFirstRunProgress, BriefingFirstRunSourceProgress
from app.models.contracts import BriefingFirstRunPhase
from app.models.db import (
    BriefingLens,
    BriefingSegment,
    OnboardingFirstEditionRun,
    OnboardingFirstEditionSource,
    UserScraperConfig,
)

TERMINAL_SOURCE_STATUSES = {"ready", "empty", "unavailable"}


@dataclass(frozen=True)
class FirstEditionSourceSpec:
    key: str
    display_name: str
    kind: str


def start_first_edition(db: Session, *, user_id: int) -> OnboardingFirstEditionRun:
    """Replace any unfinished run with one based on the user's active source configs."""

    now = _now()
    unfinished = (
        db.query(OnboardingFirstEditionRun)
        .filter(OnboardingFirstEditionRun.user_id == user_id)
        .filter(OnboardingFirstEditionRun.status.in_(("active", "ready")))
        .all()
    )
    for run in unfinished:
        run.status = "expired"
        run.completed_at = now

    specs = _source_specs(db, user_id=user_id)
    run = OnboardingFirstEditionRun(
        user_id=user_id,
        status="active",
        revision=1,
        connected_source_count=len(specs),
        ready_category_keys=[],
    )
    db.add(run)
    db.flush()
    run_id = _required_id(run.id)
    for position, spec in enumerate(specs):
        db.add(
            OnboardingFirstEditionSource(
                run_id=run_id,
                source_key=spec.key,
                display_name=spec.display_name,
                source_kind=spec.kind,
                position=position,
                status="queued",
            )
        )
    db.flush()
    return run


def get_first_run_progress(db: Session, *, user_id: int) -> BriefingFirstRunProgress | None:
    run = _active_run(db, user_id=user_id)
    if run is None:
        return None
    sources = (
        db.query(OnboardingFirstEditionSource)
        .filter(OnboardingFirstEditionSource.run_id == run.id)
        .order_by(
            OnboardingFirstEditionSource.completion_sequence.asc().nullslast(),
            OnboardingFirstEditionSource.position.asc(),
        )
        .all()
    )
    completed = [
        BriefingFirstRunSourceProgress(
            display_name=str(source.display_name),
            processed_item_count=max(int(source.processed_item_count or 0), 0),
        )
        for source in sources
        if source.status in TERMINAL_SOURCE_STATUSES
    ]
    active = [
        str(source.display_name)
        for source in sources
        if source.status not in TERMINAL_SOURCE_STATUSES
    ][:2]
    ready_keys = [str(key) for key in (run.ready_category_keys or [])]
    all_sources_done = not sources or len(completed) == len(sources)
    if all_sources_done and not ready_keys:
        phase = BriefingFirstRunPhase.WAITING_FOR_CONTENT
    elif run.status == "ready" or (all_sources_done and ready_keys):
        phase = BriefingFirstRunPhase.READY
    else:
        phase = BriefingFirstRunPhase.ACTIVE
    return BriefingFirstRunProgress(
        revision=int(run.revision or 0),
        phase=phase,
        connected_source_count=int(run.connected_source_count or 0),
        completed_sources=completed,
        active_sources=active,
        ready_category_keys=ready_keys,
    )


def mark_feed_sources_complete(
    db: Session,
    *,
    user_id: int,
    config_ids: list[int],
    processed_item_counts: dict[int, int] | None = None,
) -> int:
    keys = {f"feed:{config_id}" for config_id in config_ids}
    counts_by_key = {
        f"feed:{config_id}": max(int((processed_item_counts or {}).get(config_id, 0)), 0)
        for config_id in config_ids
    }
    return _mark_sources_complete(
        db,
        user_id=user_id,
        source_keys=keys,
        processed_item_counts=counts_by_key,
    )


def mark_scraper_sources_complete(
    db: Session,
    *,
    scraper_keys: list[str],
    processed_item_counts: dict[str, int] | None = None,
    processed_item_counts_by_config_id: dict[int, int] | None = None,
) -> int:
    normalized = {_normalized_source_key(key) for key in scraper_keys}
    if not normalized:
        return 0
    rows = (
        db.query(OnboardingFirstEditionSource)
        .join(
            OnboardingFirstEditionRun,
            OnboardingFirstEditionRun.id == OnboardingFirstEditionSource.run_id,
        )
        .filter(OnboardingFirstEditionRun.status.in_(("active", "ready")))
        .filter(OnboardingFirstEditionSource.source_kind.in_(("aggregator", "reddit")))
        .all()
    )
    normalized_counts = {
        _normalized_source_key(key): max(int(value), 0)
        for key, value in (processed_item_counts or {}).items()
    }
    matched: list[OnboardingFirstEditionSource] = []
    counts_by_key: dict[str, int] = {}
    for row in rows:
        source_key = str(row.source_key)
        if row.source_kind == "reddit":
            if "reddit" not in normalized:
                continue
            matched.append(row)
            config_id = _source_config_id(source_key)
            counts_by_key[source_key] = max(
                int((processed_item_counts_by_config_id or {}).get(config_id, 0)),
                0,
            )
            continue

        scraper_key = _normalized_source_key(source_key.split(":", 1)[-1])
        if scraper_key not in normalized:
            continue
        matched.append(row)
        counts_by_key[source_key] = normalized_counts.get(scraper_key, 0)
    return _complete_rows(db, matched, processed_item_counts=counts_by_key)


def sync_ready_categories(db: Session, *, user_id: int) -> int:
    """Append newly readable categories without reordering prior arrivals."""

    run = _active_run(db, user_id=user_id)
    if run is None:
        return 0
    rows = (
        db.query(BriefingLens.key, BriefingLens.position)
        .join(BriefingSegment, BriefingSegment.lens_id == BriefingLens.id)
        .filter(BriefingLens.user_id == user_id, BriefingLens.status == "active")
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .group_by(BriefingLens.key, BriefingLens.position)
        .order_by(BriefingLens.position.asc(), BriefingLens.key.asc())
        .all()
    )
    existing = [str(key) for key in (run.ready_category_keys or [])]
    new_keys = [str(key) for key, _ in rows if str(key) not in existing]
    if new_keys:
        run.ready_category_keys = [*existing, *new_keys]
        run.revision = int(run.revision or 0) + 1

    source_count = (
        db.query(OnboardingFirstEditionSource)
        .filter(OnboardingFirstEditionSource.run_id == run.id)
        .count()
    )
    terminal_count = (
        db.query(OnboardingFirstEditionSource)
        .filter(OnboardingFirstEditionSource.run_id == run.id)
        .filter(OnboardingFirstEditionSource.status.in_(tuple(TERMINAL_SOURCE_STATUSES)))
        .count()
    )
    if (
        (source_count == 0 or terminal_count == source_count)
        and (existing or new_keys)
        and run.status != "ready"
    ):
        run.status = "ready"
        run.ready_at = _now()
        run.revision = int(run.revision or 0) + 1
    db.flush()
    return len(new_keys)


def complete_first_edition(db: Session, *, user_id: int) -> bool:
    run = _active_run(db, user_id=user_id)
    if run is None:
        return False
    run.status = "completed"
    run.completed_at = _now()
    run.revision = int(run.revision or 0) + 1
    db.flush()
    return True


def _source_specs(db: Session, *, user_id: int) -> list[FirstEditionSourceSpec]:
    configs = (
        db.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == user_id, UserScraperConfig.is_active.is_(True))
        .order_by(UserScraperConfig.created_at.asc(), UserScraperConfig.id.asc())
        .all()
    )
    specs: list[FirstEditionSourceSpec] = []
    for config in configs:
        config_id = _required_id(config.id)
        config_data = config.config or {}
        display_name = str(config.display_name or config_data.get("name") or config.scraper_type)
        if config.scraper_type in {"substack", "atom", "podcast_rss"}:
            specs.append(
                FirstEditionSourceSpec(
                    key=f"feed:{config_id}",
                    display_name=display_name,
                    kind="feed",
                )
            )
        elif config.scraper_type == "aggregator":
            scraper_key = str(config_data.get("key") or display_name).strip().lower()
            specs.append(
                FirstEditionSourceSpec(
                    key=f"scraper:{scraper_key}",
                    display_name=display_name,
                    kind="aggregator",
                )
            )
        elif config.scraper_type == "reddit":
            subreddit = str(config_data.get("subreddit") or display_name).strip()
            specs.append(
                FirstEditionSourceSpec(
                    key=f"reddit:{config_id}",
                    display_name=f"r/{subreddit.removeprefix('r/')}",
                    kind="reddit",
                )
            )
    return specs


def _active_run(db: Session, *, user_id: int) -> OnboardingFirstEditionRun | None:
    return (
        db.query(OnboardingFirstEditionRun)
        .filter(OnboardingFirstEditionRun.user_id == user_id)
        .filter(OnboardingFirstEditionRun.status.in_(("active", "ready")))
        .order_by(OnboardingFirstEditionRun.id.desc())
        .first()
    )


def _mark_sources_complete(
    db: Session,
    *,
    user_id: int,
    source_keys: set[str],
    processed_item_counts: dict[str, int] | None = None,
) -> int:
    if not source_keys:
        return 0
    run = _active_run(db, user_id=user_id)
    if run is None:
        return 0
    rows = (
        db.query(OnboardingFirstEditionSource)
        .filter(OnboardingFirstEditionSource.run_id == run.id)
        .filter(OnboardingFirstEditionSource.source_key.in_(source_keys))
        .all()
    )
    return _complete_rows(db, rows, processed_item_counts=processed_item_counts)


def _complete_rows(
    db: Session,
    rows: list[OnboardingFirstEditionSource],
    *,
    processed_item_counts: dict[str, int] | None = None,
) -> int:
    pending = [row for row in rows if row.status not in TERMINAL_SOURCE_STATUSES]
    if not pending:
        return 0
    run_ids = {_required_id(row.run_id) for row in pending}
    next_sequence = 1
    if run_ids:
        current = (
            db.query(OnboardingFirstEditionSource.completion_sequence)
            .filter(OnboardingFirstEditionSource.run_id.in_(run_ids))
            .order_by(OnboardingFirstEditionSource.completion_sequence.desc().nullslast())
            .first()
        )
        if current and current[0] is not None:
            next_sequence = int(current[0]) + 1
    now = _now()
    for row in sorted(pending, key=lambda item: (int(item.position or 0), int(item.id or 0))):
        row.status = "ready"
        row.completion_sequence = next_sequence
        row.processed_item_count = max(
            int((processed_item_counts or {}).get(str(row.source_key), 0)),
            0,
        )
        row.completed_at = now
        next_sequence += 1
    for run_id in run_ids:
        run = (
            db.query(OnboardingFirstEditionRun)
            .filter(OnboardingFirstEditionRun.id == run_id)
            .first()
        )
        if run is not None:
            run.revision = int(run.revision or 0) + 1
    db.flush()
    return len(pending)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _required_id(value: int | None) -> int:
    if value is None:
        raise ValueError("Expected a persisted database row")
    return value


def _normalized_source_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _source_config_id(source_key: str) -> int:
    raw_id = source_key.rsplit(":", 1)[-1]
    return int(raw_id) if raw_id.isdigit() else 0
