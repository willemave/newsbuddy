"""Durable source progress for the temporary Start Here briefing page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Query, Session

from app.models.api.briefing import BriefingFirstRunProgress, BriefingFirstRunSourceProgress
from app.models.contracts import BriefingFirstRunPhase, BriefingFirstRunSourceOutcome
from app.models.db import (
    OnboardingFirstEditionRun,
    OnboardingFirstEditionSource,
    UserScraperConfig,
)

TERMINAL_SOURCE_STATUSES = {
    BriefingFirstRunSourceOutcome.PROCESSED.value,
    BriefingFirstRunSourceOutcome.UNAVAILABLE.value,
}


@dataclass(frozen=True)
class FirstEditionSourceSpec:
    key: str
    display_name: str
    kind: str


@dataclass(frozen=True)
class FirstRunValidator:
    run_id: int
    revision: int


def start_first_edition(db: Session, *, user_id: int) -> OnboardingFirstEditionRun:
    """Replace an unfinished run with one based on the user's active sources."""

    now = _now()
    unfinished = _active_run(db, user_id=user_id)
    if unfinished is not None:
        unfinished.status = "expired"
        unfinished.completed_at = now

    specs = _source_specs(db, user_id=user_id)
    run = OnboardingFirstEditionRun(user_id=user_id, status="active", revision=1)
    db.add(run)
    db.flush()
    run_id = _required_id(run.id)
    db.add_all(
        [
            OnboardingFirstEditionSource(
                run_id=run_id,
                source_key=spec.key,
                display_name=spec.display_name,
                source_kind=spec.kind,
                position=position,
                status="queued",
            )
            for position, spec in enumerate(specs)
        ]
    )
    db.flush()
    return run


def get_first_run_progress(
    db: Session,
    *,
    user_id: int,
    ready_category_keys: list[str] | None = None,
) -> BriefingFirstRunProgress | None:
    run = _active_run(db, user_id=user_id)
    if run is None:
        return None
    sources = (
        db.query(OnboardingFirstEditionSource)
        .filter(OnboardingFirstEditionSource.run_id == run.id)
        .order_by(
            OnboardingFirstEditionSource.completed_at.asc().nullslast(),
            OnboardingFirstEditionSource.position.asc(),
            OnboardingFirstEditionSource.id.asc(),
        )
        .all()
    )
    completed = [
        BriefingFirstRunSourceProgress(
            display_name=str(source.display_name),
            processed_item_count=max(int(source.processed_item_count or 0), 0),
            outcome=BriefingFirstRunSourceOutcome(str(source.status)),
        )
        for source in sources
        if source.status in TERMINAL_SOURCE_STATUSES
    ]
    active = [
        str(source.display_name)
        for source in sorted(sources, key=lambda source: int(source.position or 0))
        if source.status not in TERMINAL_SOURCE_STATUSES
    ][:2]
    category_keys = ready_category_keys or []
    all_sources_done = len(completed) == len(sources)
    if all_sources_done and category_keys:
        phase = BriefingFirstRunPhase.READY
    elif all_sources_done:
        phase = BriefingFirstRunPhase.WAITING_FOR_CONTENT
    else:
        phase = BriefingFirstRunPhase.ACTIVE
    return BriefingFirstRunProgress(
        run_id=_required_id(run.id),
        revision=int(run.revision or 0),
        phase=phase,
        connected_source_count=len(sources),
        completed_sources=completed,
        active_sources=active,
        ready_category_keys=category_keys,
    )


def get_first_run_validator(db: Session, *, user_id: int) -> FirstRunValidator | None:
    row = (
        _active_run_query(db, user_id=user_id)
        .with_entities(OnboardingFirstEditionRun.id, OnboardingFirstEditionRun.revision)
        .first()
    )
    if row is None:
        return None
    return FirstRunValidator(
        run_id=_required_id(row.id),
        revision=int(row.revision or 0),
    )


def record_feed_source_result(
    db: Session,
    *,
    run_id: int,
    config_id: int,
    processed_item_count: int,
    outcome: BriefingFirstRunSourceOutcome,
) -> bool:
    run = _lock_active_run(db, run_id=run_id)
    if run is None:
        return False
    row = (
        db.query(OnboardingFirstEditionSource)
        .filter(
            OnboardingFirstEditionSource.run_id == run_id,
            OnboardingFirstEditionSource.source_key == f"feed:{config_id}",
        )
        .with_for_update()
        .first()
    )
    if row is None:
        return False
    return (
        _record_rows(
            db,
            run=run,
            rows=[row],
            outcome=outcome,
            processed_item_counts={str(row.source_key): processed_item_count},
        )
        > 0
    )


def record_scraper_source_result(
    db: Session,
    *,
    run_id: int,
    scraper_key: str,
    processed_item_count: int,
    processed_item_counts_by_config_id: dict[int, int] | None,
    outcome: BriefingFirstRunSourceOutcome,
) -> int:
    run = _lock_active_run(db, run_id=run_id)
    if run is None:
        return 0
    normalized_key = _normalized_source_key(scraper_key)
    rows = (
        db.query(OnboardingFirstEditionSource)
        .filter(OnboardingFirstEditionSource.run_id == run_id)
        .filter(OnboardingFirstEditionSource.source_kind.in_(("aggregator", "reddit")))
        .with_for_update()
        .all()
    )
    matched: list[OnboardingFirstEditionSource] = []
    counts_by_key: dict[str, int] = {}
    for row in rows:
        source_key = str(row.source_key)
        if row.source_kind == "reddit":
            if normalized_key != "reddit":
                continue
            matched.append(row)
            config_id = _source_config_id(source_key)
            counts_by_key[source_key] = max(
                int((processed_item_counts_by_config_id or {}).get(config_id, 0)),
                0,
            )
            continue
        if _normalized_source_key(source_key.split(":", 1)[-1]) != normalized_key:
            continue
        matched.append(row)
        counts_by_key[source_key] = max(processed_item_count, 0)
    return _record_rows(
        db,
        run=run,
        rows=matched,
        outcome=outcome,
        processed_item_counts=counts_by_key,
    )


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
            specs.append(FirstEditionSourceSpec(f"feed:{config_id}", display_name, "feed"))
        elif config.scraper_type == "aggregator":
            scraper_key = str(config_data.get("key") or display_name).strip().lower()
            specs.append(
                FirstEditionSourceSpec(f"scraper:{scraper_key}", display_name, "aggregator")
            )
        elif config.scraper_type == "reddit":
            subreddit = str(config_data.get("subreddit") or display_name).strip()
            specs.append(
                FirstEditionSourceSpec(
                    f"reddit:{config_id}",
                    f"r/{subreddit.removeprefix('r/')}",
                    "reddit",
                )
            )
    return specs


def _active_run(db: Session, *, user_id: int) -> OnboardingFirstEditionRun | None:
    return _active_run_query(db, user_id=user_id).first()


def _active_run_query(
    db: Session,
    *,
    user_id: int,
) -> Query[OnboardingFirstEditionRun]:
    return (
        db.query(OnboardingFirstEditionRun)
        .filter(
            OnboardingFirstEditionRun.user_id == user_id,
            OnboardingFirstEditionRun.status == "active",
        )
        .order_by(OnboardingFirstEditionRun.id.desc())
    )


def _lock_active_run(db: Session, *, run_id: int) -> OnboardingFirstEditionRun | None:
    return (
        db.query(OnboardingFirstEditionRun)
        .filter(
            OnboardingFirstEditionRun.id == run_id,
            OnboardingFirstEditionRun.status == "active",
        )
        .with_for_update()
        .first()
    )


def _record_rows(
    db: Session,
    *,
    run: OnboardingFirstEditionRun,
    rows: list[OnboardingFirstEditionSource],
    outcome: BriefingFirstRunSourceOutcome,
    processed_item_counts: dict[str, int],
) -> int:
    now = _now()
    changed = 0
    for row in rows:
        item_count = max(int(processed_item_counts.get(str(row.source_key), 0)), 0)
        if row.status == BriefingFirstRunSourceOutcome.PROCESSED.value:
            continue
        if row.status == outcome.value and int(row.processed_item_count or 0) == item_count:
            continue
        row.status = outcome.value
        row.processed_item_count = item_count
        if row.completed_at is None:
            row.completed_at = now
        changed += 1
    if changed:
        run.revision = int(run.revision or 0) + 1
        db.flush()
    return changed


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
    return int(raw_id)
