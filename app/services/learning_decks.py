"""Business logic for Learning Deck product state."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.api.learning_decks import (
    LearningDeckResponse,
    LearningDeckRunResponse,
    LearningDeckTimelineEntry,
)
from app.models.contracts import (
    LearningDeckRunStatus,
    LearningDeckSourceKind,
    LearningDeckStatus,
    TaskStatus,
    TaskType,
)
from app.models.db import LearningDeck, LearningDeckRun, ProcessingTask, User
from app.pipeline.task_specs import get_task_spec
from app.services.learning_deck_artifacts import delete_learning_deck_objects
from app.services.learning_deck_common import (
    ACTIVE_RUN_STATUSES,
    LearningDeckError,
    LearningDeckHostedObject,
    LearningDeckSignedToken,
    LearningDeckSource,
    LearningDeckSourceNotReady,
    append_learning_deck_timeline,
    clean_optional_text,
    coerce_string_list,
    require_datetime_value,
    require_int_value,
    require_str_value,
    require_user_id,
    utcnow,
)
from app.services.learning_deck_hosting import (
    read_learning_deck_asset_object,
    read_learning_deck_source_notes_object,
    read_learning_deck_viewer_object,
)
from app.services.learning_deck_sources import (
    build_content_source_snapshot,
    build_github_source_snapshot,
    learning_deck_display_title,
    normalize_github_repository_source,
    resolve_learning_deck_create_source,
    usable_learning_deck_title,
)
from app.services.learning_deck_tokens import (
    build_private_learning_deck_token,
    decode_private_learning_deck_token,
    disable_learning_deck_share,
    enable_learning_deck_share,
    get_deck_by_private_token,
    get_deck_by_valid_share_token,
)

__all__ = [
    "LearningDeckError",
    "LearningDeckHostedObject",
    "LearningDeckSignedToken",
    "LearningDeckSource",
    "LearningDeckSourceNotReady",
    "append_learning_deck_timeline",
    "build_content_source_snapshot",
    "build_github_source_snapshot",
    "build_private_learning_deck_token",
    "create_or_rerun_learning_deck",
    "decode_private_learning_deck_token",
    "delete_learning_deck",
    "disable_learning_deck_share",
    "enable_learning_deck_share",
    "get_deck_by_private_token",
    "get_deck_by_valid_share_token",
    "get_learning_deck",
    "list_learning_decks",
    "mark_learning_deck_run_failed",
    "normalize_github_repository_source",
    "present_learning_deck",
    "present_learning_deck_run",
    "promote_learning_deck_run",
    "read_learning_deck_asset_object",
    "read_learning_deck_source_notes_object",
    "read_learning_deck_viewer_object",
]


def list_learning_decks(db: Session, *, user_id: int) -> list[LearningDeck]:
    """Return current non-deleted decks for a user."""
    return (
        db.query(LearningDeck)
        .filter(LearningDeck.user_id == user_id, LearningDeck.deleted_at.is_(None))
        .order_by(desc(LearningDeck.updated_at), desc(LearningDeck.id))
        .all()
    )


def get_learning_deck(db: Session, *, user_id: int, deck_id: int) -> LearningDeck | None:
    """Return one current deck for the owner."""
    return (
        db.query(LearningDeck)
        .filter(
            LearningDeck.id == deck_id,
            LearningDeck.user_id == user_id,
            LearningDeck.deleted_at.is_(None),
        )
        .first()
    )


def create_or_rerun_learning_deck(
    db: Session,
    *,
    current_user: User,
    content_id: int | None = None,
    news_item_id: int | None = None,
    url: str | None = None,
    interests_prompt: str | None = None,
) -> LearningDeck:
    """Create or rerun a Learning Deck and enqueue generation in one transaction."""
    user_id = require_user_id(current_user)
    _ensure_no_active_run(db, user_id=user_id)
    source = resolve_learning_deck_create_source(
        db,
        current_user=current_user,
        content_id=content_id,
        news_item_id=news_item_id,
        url=url,
    )

    try:
        _ensure_no_active_run(db, user_id=user_id)
        deck = _get_or_create_deck(db, user_id=user_id, source=source)
        run = _create_queued_run(
            deck=deck,
            user_id=user_id,
            source=source,
            interests_prompt=interests_prompt,
        )
        db.add(run)
        db.flush()

        run_id = require_int_value(run.id, "Learning Deck run id")
        deck.latest_run_id = run_id
        deck.updated_at = utcnow()
        _enqueue_generation_task(db, run_id=run_id, user_id=user_id)
        db.commit()
        db.refresh(deck)
        return deck
    except IntegrityError as exc:
        db.rollback()
        if _is_active_run_integrity_error(exc):
            raise LearningDeckError(
                "A Learning Deck is already generating",
                status_code=409,
            ) from exc
        raise


def delete_learning_deck(db: Session, *, user_id: int, deck_id: int) -> None:
    """Soft-delete deck state and hard-delete known public artifact objects."""
    deck = get_learning_deck(db, user_id=user_id, deck_id=deck_id)
    if deck is None:
        raise LearningDeckError("Learning Deck not found", status_code=404)
    runs = db.query(LearningDeckRun).filter(LearningDeckRun.deck_id == deck_id).all()
    keys: list[str] = []
    keys.extend(coerce_string_list(deck.artifact_object_keys))
    for run in runs:
        keys.extend(coerce_string_list(run.artifact_object_keys))
    delete_learning_deck_objects(keys)
    deck.deleted_at = utcnow()
    deck.share_enabled = False
    deck.updated_at = utcnow()
    db.commit()


def present_learning_deck(db: Session, deck: LearningDeck) -> LearningDeckResponse:
    """Build an API response for one deck."""
    latest_run = _latest_run_for_deck(db, deck)
    status = LearningDeckStatus.READY if deck.latest_successful_run_id else None
    if latest_run is not None and latest_run.status != LearningDeckRunStatus.COMPLETED.value:
        status = LearningDeckStatus(
            require_str_value(latest_run.status, "Learning Deck run status")
        )
    display_title = learning_deck_display_title(db, deck)
    return LearningDeckResponse(
        id=require_int_value(deck.id, "Learning Deck id"),
        title=display_title,
        source_kind=LearningDeckSourceKind(
            require_str_value(deck.source_kind, "Learning Deck source kind")
        ),
        source_url=deck.source_url,
        source_content_id=deck.source_content_id,
        source_title=display_title,
        source_metadata=deck.source_metadata if isinstance(deck.source_metadata, dict) else {},
        status=status,
        share_enabled=bool(deck.share_enabled),
        viewer_available=bool(deck.deck_object_key and deck.latest_successful_run_id),
        source_notes_available=bool(
            deck.source_notes_html_object_key and deck.latest_successful_run_id
        ),
        latest_successful_run_id=deck.latest_successful_run_id,
        latest_run=present_learning_deck_run(latest_run) if latest_run else None,
        created_at=require_datetime_value(deck.created_at, "Learning Deck created_at"),
        updated_at=deck.updated_at,
    )


def present_learning_deck_run(run: LearningDeckRun) -> LearningDeckRunResponse:
    """Build an API response for one run."""
    return LearningDeckRunResponse(
        id=require_int_value(run.id, "Learning Deck run id"),
        status=LearningDeckRunStatus(require_str_value(run.status, "Learning Deck run status")),
        interests_prompt=run.interests_prompt,
        timeline=[
            LearningDeckTimelineEntry.model_validate(entry)
            for entry in (run.timeline or [])
            if isinstance(entry, dict)
        ],
        error_message=run.error_message,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=require_datetime_value(run.created_at, "Learning Deck run created_at"),
        updated_at=run.updated_at,
    )


def mark_learning_deck_run_failed(
    db: Session,
    run: LearningDeckRun,
    *,
    error_message: str,
) -> None:
    """Persist a failed run without replacing the deck's latest successful artifact."""
    run.status = LearningDeckRunStatus.FAILED.value
    run.error_message = error_message[:4000]
    run.completed_at = utcnow()
    append_learning_deck_timeline(
        run,
        status=LearningDeckRunStatus.FAILED,
        note="Learning Deck generation failed",
    )
    run_id = require_int_value(run.id, "Learning Deck run id")
    deck_id = require_int_value(run.deck_id, "Learning Deck id")
    deck = db.query(LearningDeck).filter(LearningDeck.id == deck_id).first()
    if deck is not None:
        deck.latest_run_id = run_id
        deck.updated_at = utcnow()
    db.commit()


def promote_learning_deck_run(
    db: Session,
    run: LearningDeckRun,
    *,
    artifact_storage_prefix: str,
    deck_object_key: str,
    source_notes_object_key: str,
    source_notes_html_object_key: str,
    artifact_object_keys: list[str],
    title: str | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> None:
    """Promote a completed run to the deck's latest successful artifact."""
    run_id = require_int_value(run.id, "Learning Deck run id")
    deck_id = require_int_value(run.deck_id, "Learning Deck id")
    deck = db.query(LearningDeck).filter(LearningDeck.id == deck_id).first()
    if deck is None:
        raise LearningDeckError("Learning Deck not found", status_code=404)
    old_keys = coerce_string_list(deck.artifact_object_keys)
    run.status = LearningDeckRunStatus.COMPLETED.value
    run.completed_at = utcnow()
    run.artifact_storage_prefix = artifact_storage_prefix
    run.deck_object_key = deck_object_key
    run.source_notes_object_key = source_notes_object_key
    run.source_notes_html_object_key = source_notes_html_object_key
    run.artifact_object_keys = artifact_object_keys
    append_learning_deck_timeline(
        run,
        status=LearningDeckRunStatus.COMPLETED,
        note="Learning Deck is ready",
    )

    deck.latest_successful_run_id = run_id
    deck.latest_run_id = run_id
    deck.artifact_storage_prefix = artifact_storage_prefix
    deck.deck_object_key = deck_object_key
    deck.source_notes_object_key = source_notes_object_key
    deck.source_notes_html_object_key = source_notes_html_object_key
    deck.artifact_object_keys = artifact_object_keys
    if title:
        deck.title = title[:500]
    if source_metadata:
        metadata = dict(deck.source_metadata or {})
        metadata.update(source_metadata)
        deck.source_metadata = metadata
    deck.updated_at = utcnow()
    db.commit()

    new_keys = set(coerce_string_list(artifact_object_keys))
    stale_keys = [key for key in old_keys if key not in new_keys]
    if stale_keys:
        delete_learning_deck_objects(stale_keys)


def _create_queued_run(
    *,
    deck: LearningDeck,
    user_id: int,
    source: LearningDeckSource,
    interests_prompt: str | None,
) -> LearningDeckRun:
    deck_id = require_int_value(deck.id, "Learning Deck id")
    run = LearningDeckRun(
        deck_id=deck_id,
        user_id=user_id,
        status=LearningDeckRunStatus.QUEUED.value,
        interests_prompt=clean_optional_text(interests_prompt),
        source_snapshot={
            "source_kind": source.source_kind.value,
            "source_identity": source.source_identity,
            "source_url": source.source_url,
            "source_content_id": source.source_content_id,
            "source_title": source.source_title,
            "source_metadata": source.source_metadata,
        },
        timeline=[],
        artifact_object_keys=[],
    )
    append_learning_deck_timeline(
        run,
        status=LearningDeckRunStatus.QUEUED,
        note="Learning Deck generation queued",
    )
    return run


def _get_or_create_deck(
    db: Session,
    *,
    user_id: int,
    source: LearningDeckSource,
) -> LearningDeck:
    deck = (
        db.query(LearningDeck)
        .filter(
            LearningDeck.user_id == user_id,
            LearningDeck.source_identity == source.source_identity,
            LearningDeck.deleted_at.is_(None),
        )
        .first()
    )
    if deck is None:
        deck = LearningDeck(
            user_id=user_id,
            source_kind=source.source_kind.value,
            source_identity=source.source_identity,
            source_url=source.source_url,
            source_content_id=source.source_content_id,
            source_title=source.source_title,
            source_metadata=source.source_metadata,
            title=source.source_title,
            artifact_object_keys=[],
            share_enabled=False,
        )
        db.add(deck)
        db.flush()
        return deck

    deck.source_kind = source.source_kind.value
    deck.source_url = source.source_url
    deck.source_content_id = source.source_content_id
    deck.source_title = source.source_title
    deck.source_metadata = source.source_metadata
    if not usable_learning_deck_title(deck.title):
        deck.title = source.source_title
    deck.updated_at = utcnow()
    db.flush()
    return deck


def _enqueue_generation_task(db: Session, *, run_id: int, user_id: int) -> int:
    """Insert the generation queue task in the caller's transaction."""
    task_spec = get_task_spec(TaskType.GENERATE_LEARNING_DECK)
    payload = task_spec.normalize_payload({"learning_deck_run_id": run_id, "user_id": user_id})
    task = ProcessingTask(
        task_type=TaskType.GENERATE_LEARNING_DECK.value,
        payload=payload,
        status=TaskStatus.PENDING.value,
        queue_name=task_spec.queue.value,
        available_at=utcnow(),
    )
    db.add(task)
    db.flush()
    if task.id is None:
        raise LearningDeckError("Learning Deck generation task was not created", status_code=500)

    db.execute(
        select(
            func.pg_notify(
                "processing_tasks",
                json.dumps(
                    {
                        "task_id": int(task.id),
                        "task_type": TaskType.GENERATE_LEARNING_DECK.value,
                        "queue_name": task_spec.queue.value,
                    },
                    separators=(",", ":"),
                ),
            )
        )
    )
    return int(task.id)


def _ensure_no_active_run(db: Session, *, user_id: int) -> None:
    active = (
        db.query(LearningDeckRun.id)
        .filter(LearningDeckRun.user_id == user_id, LearningDeckRun.status.in_(ACTIVE_RUN_STATUSES))
        .first()
    )
    if active is not None:
        raise LearningDeckError("A Learning Deck is already generating", status_code=409)


def _latest_run_for_deck(db: Session, deck: LearningDeck) -> LearningDeckRun | None:
    deck_id = require_int_value(deck.id, "Learning Deck id")
    if deck.latest_run_id:
        run = db.query(LearningDeckRun).filter(LearningDeckRun.id == deck.latest_run_id).first()
        if run is not None:
            return run
    return (
        db.query(LearningDeckRun)
        .filter(LearningDeckRun.deck_id == deck_id)
        .order_by(desc(LearningDeckRun.created_at), desc(LearningDeckRun.id))
        .first()
    )


def _is_active_run_integrity_error(exc: IntegrityError) -> bool:
    text = str(exc.orig if exc.orig is not None else exc).lower()
    return "uq_learning_deck_runs_user_active" in text
