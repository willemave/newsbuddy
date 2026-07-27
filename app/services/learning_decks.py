"""Business logic for Learning Deck product state."""

from __future__ import annotations

import json
from dataclasses import replace
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
    LlmTaskApprovalPolicy,
    LlmTaskKind,
    LlmTaskMode,
    LlmTaskStatus,
    LlmWorkflowState,
    TaskStatus,
    TaskType,
)
from app.models.db import LearningDeck, LearningDeckRun, LlmTask, ProcessingTask, User
from app.pipeline.task_specs import get_task_spec
from app.services.learning_deck_artifacts import (
    StoredLearningDeckArtifact,
    delete_learning_deck_objects,
)
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
from app.services.learning_deck_publication import commit_learning_deck_artifact_promotion
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
from app.services.llm_tasks import create_llm_task, set_llm_task_status

ACTIVE_LLM_TASK_STATUSES = {
    LlmTaskStatus.QUEUED.value,
    LlmTaskStatus.PREPARING.value,
    LlmTaskStatus.RUNNING.value,
    LlmTaskStatus.AWAITING_APPROVAL.value,
    LlmTaskStatus.APPLYING.value,
}

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
    submitted_via: str | None = None,
    share_action_task_id: int | None = None,
) -> LearningDeck:
    """Create or rerun a Learning Deck and enqueue generation in one transaction."""
    user_id = require_user_id(current_user)
    source = resolve_learning_deck_create_source(
        db,
        current_user=current_user,
        content_id=content_id,
        news_item_id=news_item_id,
        url=url,
    )
    source = _with_submission_metadata(
        source,
        submitted_via=submitted_via,
        share_action_task_id=share_action_task_id,
    )

    try:
        deck = _get_or_create_deck(db, user_id=user_id, source=source)
        active_task = _active_learning_deck_task(db, user_id=user_id)
        if active_task is not None:
            if active_task.subject_id == deck.id:
                db.commit()
                db.refresh(deck)
                return deck
            db.rollback()
            raise LearningDeckError("A Learning Deck is already generating", status_code=409)
        llm_task = create_llm_task(
            db,
            user_id=user_id,
            task_kind=LlmTaskKind.LEARNING_DECK,
            mode=LlmTaskMode.LEARNING_DECK_PRESENTATION,
            workflow_key="learning_deck.presentation.v1",
            subject_id=require_int_value(deck.id, "Learning Deck id"),
            approval_policy={"default": LlmTaskApprovalPolicy.AUTO_APPLY.value},
            allowed_actions=["create_learning_deck"],
            tool_policy={"execute_bash": True, "web_search": True, "files": "read_write"},
            prompt_pack="learning_deck.presentation",
            input_json={
                "deck_id": require_int_value(deck.id, "Learning Deck id"),
                "source": {
                    "source_kind": source.source_kind.value,
                    "source_identity": source.source_identity,
                    "source_url": source.source_url,
                    "source_content_id": source.source_content_id,
                    "source_title": source.source_title,
                    "source_metadata": source.source_metadata,
                },
                "interests_prompt": clean_optional_text(interests_prompt),
            },
        )
        llm_task_id = require_int_value(llm_task.id, "LLM task id")
        deck.latest_task_id = llm_task_id
        deck.updated_at = utcnow()
        _enqueue_llm_task(db, llm_task_id=llm_task_id, user_id=user_id)
        db.commit()
        db.refresh(deck)
        return deck
    except IntegrityError as exc:
        db.rollback()
        if _is_active_task_integrity_error(exc):
            raise LearningDeckError(
                "A Learning Deck is already generating",
                status_code=409,
            ) from exc
        raise


def delete_learning_deck(db: Session, *, user_id: int, deck_id: int) -> None:
    """Soft-delete deck state and hard-delete known public artifact objects."""
    deck = (
        db.query(LearningDeck)
        .filter(
            LearningDeck.id == deck_id,
            LearningDeck.user_id == user_id,
            LearningDeck.deleted_at.is_(None),
        )
        .with_for_update()
        .first()
    )
    if deck is None:
        raise LearningDeckError("Learning Deck not found", status_code=404)
    runs = db.query(LearningDeckRun).filter(LearningDeckRun.deck_id == deck_id).all()
    keys: list[str] = []
    keys.extend(coerce_string_list(deck.artifact_object_keys))
    for run in runs:
        keys.extend(coerce_string_list(run.artifact_object_keys))

    active_tasks = (
        db.query(LlmTask)
        .filter(
            LlmTask.task_kind == LlmTaskKind.LEARNING_DECK.value,
            LlmTask.subject_id == deck_id,
            LlmTask.status.in_(ACTIVE_LLM_TASK_STATUSES),
        )
        .all()
    )
    for task in active_tasks:
        set_llm_task_status(
            db,
            task,
            status=LlmTaskStatus.CANCELLED,
            workflow_state=LlmWorkflowState.CANCELLED,
            note="Learning Deck was deleted",
            error_type="deck_deleted",
            error_message="Learning Deck was deleted",
        )

    for run in runs:
        if run.status not in ACTIVE_RUN_STATUSES:
            continue
        run.status = LearningDeckRunStatus.CANCELLED.value
        run.error_message = "Learning Deck was deleted"
        run.completed_at = utcnow()
        append_learning_deck_timeline(
            run,
            status=LearningDeckRunStatus.CANCELLED,
            note="Learning Deck was deleted",
        )

    deck.deleted_at = utcnow()
    deck.share_enabled = False
    deck.updated_at = utcnow()
    db.commit()
    delete_learning_deck_objects(keys)


def present_learning_deck(db: Session, deck: LearningDeck) -> LearningDeckResponse:
    """Build an API response for one deck."""
    latest_task = _latest_task_for_deck(db, deck)
    latest_run = None if latest_task is not None else _latest_run_for_deck(db, deck)
    successful_attempt_id = deck.latest_successful_task_id or deck.latest_successful_run_id
    status = LearningDeckStatus.READY if successful_attempt_id else None
    attempt_status = (
        latest_task.status if latest_task is not None else getattr(latest_run, "status", None)
    )
    if attempt_status is not None and attempt_status != LearningDeckRunStatus.COMPLETED.value:
        status = LearningDeckStatus(
            _learning_deck_status_for_task_status(require_str_value(attempt_status, "status"))
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
        viewer_available=bool(deck.deck_object_key and successful_attempt_id),
        source_notes_available=bool(deck.source_notes_html_object_key and successful_attempt_id),
        latest_successful_run_id=successful_attempt_id,
        latest_run=(
            _present_learning_deck_task(latest_task)
            if latest_task is not None
            else present_learning_deck_run(latest_run)
            if latest_run
            else None
        ),
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
    _set_learning_run_llm_task_status(
        db,
        run,
        status=LlmTaskStatus.FAILED,
        workflow_state=LlmWorkflowState.FAILED,
        note="Learning Deck generation failed",
        error_type="learning_deck_generation_failed",
        error_message=error_message[:4000],
    )
    db.commit()


def promote_learning_deck_run(
    db: Session,
    run: LearningDeckRun,
    *,
    artifact: StoredLearningDeckArtifact,
    title: str | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> None:
    """Promote a completed run to the deck's latest successful artifact."""
    run_id = require_int_value(run.id, "Learning Deck run id")
    deck_id = require_int_value(run.deck_id, "Learning Deck id")
    deck = db.query(LearningDeck).filter(LearningDeck.id == deck_id).with_for_update().first()
    if deck is None:
        raise LearningDeckError("Learning Deck not found", status_code=404)
    run.status = LearningDeckRunStatus.COMPLETED.value
    run.completed_at = utcnow()
    run.artifact_storage_prefix = artifact.storage_prefix
    run.deck_object_key = artifact.deck_object_key
    run.source_notes_object_key = artifact.source_notes_object_key
    run.source_notes_html_object_key = artifact.source_notes_html_object_key
    run.artifact_object_keys = artifact.artifact_object_keys
    append_learning_deck_timeline(
        run,
        status=LearningDeckRunStatus.COMPLETED,
        note="Learning Deck is ready",
    )

    _set_learning_run_llm_task_status(
        db,
        run,
        status=LlmTaskStatus.COMPLETED,
        workflow_state=LlmWorkflowState.COMPLETED,
        note="Learning Deck is ready",
        output_json={
            "learning_deck_run_id": run_id,
            "deck_id": deck_id,
            "deck_object_key": artifact.deck_object_key,
            "source_notes_object_key": artifact.source_notes_object_key,
            "source_notes_html_object_key": artifact.source_notes_html_object_key,
            "artifact_object_keys": artifact.artifact_object_keys,
        },
        artifact_manifest={
            "artifact_storage_prefix": artifact.storage_prefix,
            "deck_object_key": artifact.deck_object_key,
            "source_notes_object_key": artifact.source_notes_object_key,
            "source_notes_html_object_key": artifact.source_notes_html_object_key,
            "artifact_object_keys": artifact.artifact_object_keys,
        },
    )
    commit_learning_deck_artifact_promotion(
        db,
        deck,
        artifact=artifact,
        latest_run_id=run_id,
        title=title,
        source_metadata=source_metadata,
    )


def _with_submission_metadata(
    source: LearningDeckSource,
    *,
    submitted_via: str | None,
    share_action_task_id: int | None,
) -> LearningDeckSource:
    submission_channel = clean_optional_text(submitted_via)
    if submission_channel is None:
        return source

    metadata = dict(source.source_metadata or {})
    raw_submission = metadata.get("submission")
    submission_metadata = dict(raw_submission) if isinstance(raw_submission, dict) else {}
    submission_metadata["submitted_via"] = submission_channel
    if share_action_task_id is not None:
        submission_metadata["share_action_task_id"] = int(share_action_task_id)
    metadata["submission"] = submission_metadata
    return replace(source, source_metadata=metadata)


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
    deck.source_metadata = _merged_source_metadata(
        existing=deck.source_metadata,
        incoming=source.source_metadata,
    )
    if not usable_learning_deck_title(deck.title):
        deck.title = source.source_title
    deck.updated_at = utcnow()
    db.flush()
    return deck


def _merged_source_metadata(
    *,
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = dict(incoming or {})
    if "submission" in metadata:
        return metadata
    existing_submission = (existing or {}).get("submission") if isinstance(existing, dict) else None
    if isinstance(existing_submission, dict):
        metadata["submission"] = dict(existing_submission)
    return metadata


def _enqueue_llm_task(db: Session, *, llm_task_id: int, user_id: int) -> int:
    """Insert the generation queue task in the caller's transaction."""
    task_spec = get_task_spec(TaskType.RUN_LLM_TASK)
    payload = task_spec.normalize_payload({"llm_task_id": llm_task_id, "user_id": user_id})
    task = ProcessingTask(
        task_type=TaskType.RUN_LLM_TASK.value,
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
                        "task_type": TaskType.RUN_LLM_TASK.value,
                        "queue_name": task_spec.queue.value,
                    },
                    separators=(",", ":"),
                ),
            )
        )
    )
    return int(task.id)


def _active_learning_deck_task(db: Session, *, user_id: int) -> LlmTask | None:
    return (
        db.query(LlmTask)
        .filter(
            LlmTask.user_id == user_id,
            LlmTask.task_kind == LlmTaskKind.LEARNING_DECK.value,
            LlmTask.status.in_(ACTIVE_LLM_TASK_STATUSES),
        )
        .order_by(desc(LlmTask.created_at), desc(LlmTask.id))
        .first()
    )


def _latest_task_for_deck(db: Session, deck: LearningDeck) -> LlmTask | None:
    if deck.latest_task_id:
        task = db.query(LlmTask).filter(LlmTask.id == deck.latest_task_id).first()
        if task is not None:
            return task
    return (
        db.query(LlmTask)
        .filter(
            LlmTask.task_kind == LlmTaskKind.LEARNING_DECK.value,
            LlmTask.subject_id == deck.id,
        )
        .order_by(desc(LlmTask.created_at), desc(LlmTask.id))
        .first()
    )


def _present_learning_deck_task(task: LlmTask) -> LearningDeckRunResponse:
    timeline = []
    for entry in task.status_history or []:
        if not isinstance(entry, dict) or not entry.get("note") or not entry.get("created_at"):
            continue
        timeline.append(
            LearningDeckTimelineEntry.model_validate(
                {
                    "status": _learning_deck_status_for_task_status(str(entry.get("status"))),
                    "note": entry["note"],
                    "created_at": entry["created_at"],
                }
            )
        )
    input_json = task.input_json if isinstance(task.input_json, dict) else {}
    return LearningDeckRunResponse(
        id=require_int_value(task.id, "LLM task id"),
        status=LearningDeckRunStatus(_learning_deck_status_for_task_status(str(task.status))),
        interests_prompt=clean_optional_text(input_json.get("interests_prompt")),
        timeline=timeline,
        error_message=task.error_message,
        started_at=task.started_at,
        completed_at=task.completed_at,
        created_at=require_datetime_value(task.created_at, "LLM task created_at"),
        updated_at=task.updated_at,
    )


def _learning_deck_status_for_task_status(status: str) -> str:
    return {
        LlmTaskStatus.RUNNING.value: LearningDeckRunStatus.GENERATING.value,
        LlmTaskStatus.AWAITING_APPROVAL.value: LearningDeckRunStatus.GENERATING.value,
        LlmTaskStatus.APPLYING.value: LearningDeckRunStatus.PUBLISHING.value,
        LlmTaskStatus.CANCELLED.value: LearningDeckRunStatus.FAILED.value,
    }.get(status, status)


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


def _is_active_task_integrity_error(exc: IntegrityError) -> bool:
    text = str(exc.orig if exc.orig is not None else exc).lower()
    return "uq_llm_tasks_learning_deck_user_active" in text


def _set_learning_run_llm_task_status(
    db: Session,
    run: LearningDeckRun,
    *,
    status: LlmTaskStatus,
    workflow_state: LlmWorkflowState,
    note: str,
    error_type: str | None = None,
    error_message: str | None = None,
    output_json: dict[str, Any] | None = None,
    artifact_manifest: dict[str, Any] | None = None,
) -> None:
    """Mirror Learning Deck run status onto its generic LLM task when present."""
    if not run.llm_task_id:
        return
    task = db.query(LlmTask).filter(LlmTask.id == run.llm_task_id).first()
    if task is None:
        return
    set_llm_task_status(
        db,
        task,
        status=status,
        workflow_state=workflow_state,
        note=note,
        error_type=error_type,
        error_message=error_message,
        output_json=output_json,
        artifact_manifest=artifact_manifest,
    )
