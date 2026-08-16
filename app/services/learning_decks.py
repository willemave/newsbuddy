"""Business logic for Learning Deck product state."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sqlalchemy import desc
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
    TaskType,
)
from app.models.db import LearningDeck, LearningDeckRun, LlmTask, User
from app.services.gateways.task_queue_gateway import get_task_queue_gateway
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
    learning_deck_display_title,
    normalize_github_repository_source,
    persisted_learning_deck_source,
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
from app.services.queue import TaskEnqueueRequest

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
    "build_private_learning_deck_token",
    "create_or_rerun_learning_deck",
    "create_or_rerun_learning_deck_from_source",
    "decode_private_learning_deck_token",
    "delete_learning_deck",
    "disable_learning_deck_share",
    "enable_learning_deck_share",
    "get_deck_by_private_token",
    "get_deck_by_valid_share_token",
    "get_learning_deck",
    "list_learning_decks",
    "normalize_github_repository_source",
    "present_learning_deck",
    "present_learning_deck_run",
    "read_learning_deck_asset_object",
    "read_learning_deck_source_notes_object",
    "read_learning_deck_viewer_object",
    "retry_learning_deck",
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
    source = resolve_learning_deck_create_source(
        db,
        current_user=current_user,
        content_id=content_id,
        news_item_id=news_item_id,
        url=url,
    )
    return create_or_rerun_learning_deck_from_source(
        db,
        current_user=current_user,
        source=source,
        interests_prompt=interests_prompt,
        submitted_via=submitted_via,
        share_action_task_id=share_action_task_id,
    )


def create_or_rerun_learning_deck_from_source(
    db: Session,
    *,
    current_user: User,
    source: LearningDeckSource,
    interests_prompt: str | None = None,
    submitted_via: str | None = None,
    share_action_task_id: int | None = None,
) -> LearningDeck:
    """Create or rerun a deck from a source already resolved by a trusted workflow."""
    user_id = require_user_id(current_user)
    source = _with_submission_metadata(
        source,
        submitted_via=submitted_via,
        share_action_task_id=share_action_task_id,
    )

    try:
        deck = _get_or_create_deck(db, user_id=user_id, source=source)
        return _enqueue_learning_deck_attempt(
            db,
            deck=deck,
            source=source,
            user_id=user_id,
            interests_prompt=interests_prompt,
        )
    except IntegrityError as exc:
        db.rollback()
        if _is_active_task_integrity_error(exc):
            raise LearningDeckError(
                "A Learning Deck is already generating",
                status_code=409,
            ) from exc
        raise


def retry_learning_deck(db: Session, *, user_id: int, deck_id: int) -> LearningDeck:
    """Create a new attempt for an owned deck whose latest attempt is terminal."""
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

    active_task = _active_learning_deck_task(db, user_id=user_id)
    if active_task is not None:
        active_input = active_task.input_json if isinstance(active_task.input_json, dict) else {}
        if active_task.subject_id == deck.id and isinstance(
            active_input.get("retry_of_attempt_id"), int
        ):
            db.commit()
            db.refresh(deck)
            return deck
        db.rollback()
        message = (
            "Learning Deck does not have a failed attempt"
            if active_task.subject_id == deck.id
            else "A Learning Deck is already generating"
        )
        raise LearningDeckError(message, status_code=409)

    latest_task = _latest_task_for_deck(db, deck)
    latest_run = None if latest_task is not None else _latest_run_for_deck(db, deck)
    latest_status = (
        latest_task.status if latest_task is not None else getattr(latest_run, "status", None)
    )
    if latest_status not in {
        LlmTaskStatus.FAILED.value,
        LlmTaskStatus.CANCELLED.value,
    }:
        raise LearningDeckError("Learning Deck does not have a failed attempt", status_code=409)

    interests_prompt = (
        clean_optional_text(
            (latest_task.input_json if isinstance(latest_task.input_json, dict) else {}).get(
                "interests_prompt"
            )
        )
        if latest_task is not None
        else clean_optional_text(getattr(latest_run, "interests_prompt", None))
    )
    source = persisted_learning_deck_source(db, deck)
    retry_of_attempt_id = require_int_value(
        latest_task.id if latest_task is not None else getattr(latest_run, "id", None),
        "Learning Deck attempt id",
    )
    try:
        return _enqueue_learning_deck_attempt(
            db,
            deck=deck,
            source=source,
            user_id=user_id,
            interests_prompt=interests_prompt,
            retry_of_attempt_id=retry_of_attempt_id,
        )
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
        error_message=_public_learning_deck_error_message(None, run.error_message),
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=require_datetime_value(run.created_at, "Learning Deck run created_at"),
        updated_at=run.updated_at,
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
    if (
        deck is None
        and source.source_kind == LearningDeckSourceKind.CONTENT
        and source.source_content_id is not None
    ):
        deck = (
            db.query(LearningDeck)
            .filter(
                LearningDeck.user_id == user_id,
                LearningDeck.source_content_id == source.source_content_id,
                LearningDeck.deleted_at.is_(None),
            )
            .order_by(desc(LearningDeck.updated_at), desc(LearningDeck.id))
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
    deck.source_identity = source.source_identity
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


def _enqueue_learning_deck_attempt(
    db: Session,
    *,
    deck: LearningDeck,
    source: LearningDeckSource,
    user_id: int,
    interests_prompt: str | None,
    retry_of_attempt_id: int | None = None,
) -> LearningDeck:
    active_task = _active_learning_deck_task(db, user_id=user_id)
    if active_task is not None:
        if active_task.subject_id == deck.id:
            db.commit()
            db.refresh(deck)
            return deck
        db.rollback()
        raise LearningDeckError("A Learning Deck is already generating", status_code=409)

    input_json: dict[str, Any] = {
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
    }
    if retry_of_attempt_id is not None:
        input_json["retry_of_attempt_id"] = retry_of_attempt_id

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
        input_json=input_json,
    )
    llm_task_id = require_int_value(llm_task.id, "LLM task id")
    deck.latest_task_id = llm_task_id
    deck.updated_at = utcnow()
    _enqueue_llm_task(db, llm_task_id=llm_task_id, user_id=user_id)
    db.commit()
    db.refresh(deck)
    return deck


def _enqueue_llm_task(db: Session, *, llm_task_id: int, user_id: int) -> int:
    """Insert the generation queue task in the caller's transaction."""
    return get_task_queue_gateway().enqueue_many_in_session(
        db,
        [
            TaskEnqueueRequest(
                TaskType.RUN_LLM_TASK,
                payload={"llm_task_id": llm_task_id, "user_id": user_id},
                owner_user_id=user_id,
            )
        ],
    )[0]


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
        error_message=_public_learning_deck_error_message(task.error_type, task.error_message),
        started_at=task.started_at,
        completed_at=task.completed_at,
        created_at=require_datetime_value(task.created_at, "LLM task created_at"),
        updated_at=task.updated_at,
    )


def _public_learning_deck_error_message(
    error_type: str | None,
    error_message: str | None,
) -> str | None:
    if not error_message:
        return None
    public_errors = {
        "source_not_found": "Source content no longer exists",
        "source_processing_failed": "Source content processing failed. Please try again.",
        "source_text_unavailable": "Source content does not have readable text",
        "source_pipeline_stalled": "Source content is still being prepared. Please try again.",
    }
    mapped = public_errors.get(error_type) if error_type is not None else None
    if mapped is not None:
        return mapped
    lowered = error_message.lower()
    if any(marker in lowered for marker in ("[sql:", "sqlalchemy", "psycopg", "unique constraint")):
        return "Learning Deck generation failed. Please try again."
    return error_message


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
