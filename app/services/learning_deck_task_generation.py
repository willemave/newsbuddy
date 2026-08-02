"""Learning Deck generation owned by the generic LLM task ledger."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn

from sqlalchemy.orm import Session

from app.models.contracts import (
    ContentStatus,
    LearningDeckSourceKind,
    LlmTaskKind,
    LlmTaskStatus,
    LlmWorkflowState,
    TaskStatus,
    TaskType,
)
from app.models.db import Content, LearningDeck, LlmTask, ProcessingTask
from app.services.learning_deck_agent import (
    LearningDeckAgentExecutionError,
    LearningDeckAgentResult,
    run_learning_deck_agent,
)
from app.services.learning_deck_artifacts import (
    LearningDeckArtifactError,
    delete_learning_deck_objects,
    render_source_notes_html,
    store_learning_deck_agent_log,
    store_learning_deck_artifact,
)
from app.services.learning_deck_common import LearningDeckSourceNotReady, require_int_value
from app.services.learning_deck_generation import LearningDeckGenerationWaiting
from app.services.learning_deck_publication import commit_learning_deck_artifact_promotion
from app.services.learning_deck_sources import (
    build_content_source_snapshot_for_deck,
    build_github_source_snapshot_for_deck,
)
from app.services.llm_tasks import (
    LlmTaskError,
    require_llm_task_id,
    set_llm_task_status,
    utcnow,
)

TERMINAL_LLM_TASK_STATUSES = {
    LlmTaskStatus.COMPLETED.value,
    LlmTaskStatus.FAILED.value,
    LlmTaskStatus.CANCELLED.value,
}
SOURCE_PREPARATION_TASK_TYPES = {
    TaskType.ANALYZE_URL.value,
    TaskType.PROCESS_CONTENT.value,
    TaskType.PROCESS_PODCAST_MEDIA.value,
    TaskType.DOWNLOAD_TWEET_VIDEO_AUDIO.value,
    TaskType.TRANSCRIBE_TWEET_VIDEO.value,
}


def run_learning_deck_task(
    db: Session,
    *,
    llm_task_id: int,
    ensure_lease: Callable[[], bool] | None = None,
    agent_runner: Callable[..., LearningDeckAgentResult] | None = None,
) -> LlmTask:
    """Generate and publish one Learning Deck using only ``llm_tasks`` attempt state."""
    task = db.query(LlmTask).filter(LlmTask.id == llm_task_id).first()
    if task is None or task.task_kind != LlmTaskKind.LEARNING_DECK.value:
        raise LlmTaskError("Learning Deck LLM task not found")
    if task.status in TERMINAL_LLM_TASK_STATUSES:
        return task
    if task.subject_id is None:
        _fail_task(db, task, "invalid_subject", "Learning Deck task is missing subject_id")

    deck = db.query(LearningDeck).filter(LearningDeck.id == task.subject_id).first()
    if deck is None:
        _fail_task(db, task, "deck_not_found", "Learning Deck not found")
    if deck.deleted_at is not None:
        return _cancel_task(db, task, "Learning Deck was deleted")

    _set_status(db, task, deck, LlmTaskStatus.PREPARING, "Preparing source material")
    try:
        source_snapshot = _build_source_snapshot(db, deck)
    except LearningDeckSourceNotReady as exc:
        terminal_error = _source_terminal_error(db, deck)
        if terminal_error is not None:
            error_type, message = terminal_error
            _fail_task(db, task, error_type, message)
        _set_status(db, task, deck, LlmTaskStatus.PREPARING, str(exc))
        raise LearningDeckGenerationWaiting(
            str(exc),
            retry_delay_seconds=_source_wait_delay_seconds(task),
        ) from exc

    task.input_json = {
        **_mapping(task.input_json),
        "source": _persistable_snapshot(source_snapshot),
    }
    _set_status(db, task, deck, LlmTaskStatus.RUNNING, "Running Learning Deck agent")
    task_id = require_llm_task_id(task)
    user_id = require_int_value(task.user_id, "LLM task user id")
    deck_id = require_int_value(deck.id, "Learning Deck id")
    interests_prompt = _optional_text(_mapping(task.input_json).get("interests_prompt"))

    try:
        result = (agent_runner or run_learning_deck_agent)(
            source_snapshot=source_snapshot,
            interests_prompt=interests_prompt,
            user_id=user_id,
            run_id=task_id,
            llm_task=task,
        )
        publish_state = _reload_publishable_state(
            db,
            task_id=task_id,
            deck_id=deck_id,
        )
        if publish_state is None:
            return _reload_task(db, task_id)
        task, deck = publish_state
        _store_agent_log(db, task, deck, result.agent_log_events)
        set_llm_task_status(
            db,
            task,
            status=LlmTaskStatus.RUNNING,
            workflow_state=LlmWorkflowState.RUNNING,
            note="Validating generated artifact",
            model_provider=result.model_provider,
            model_name=result.model_name,
            sandbox_provider=result.sandbox_provider,
            sandbox_id=result.sandbox_id,
        )
        db.commit()
        source_notes_html = render_source_notes_html(
            result.source_notes_md,
            title=f"{deck.title} Source Notes",
        )
        if ensure_lease is not None and not ensure_lease():
            _fail_task(
                db,
                task,
                "lease_lost",
                "Queue lease was lost before Learning Deck publication",
            )
        stored = store_learning_deck_artifact(
            user_id=user_id,
            deck_id=deck_id,
            run_id=task_id,
            index_html=result.index_html,
            source_notes_md=result.source_notes_md,
            source_notes_html=source_notes_html,
            extra_assets=result.assets,
        )
    except LearningDeckAgentExecutionError as exc:
        db.rollback()
        _store_agent_log(db, task, deck, exc.agent_log_events)
        task.sandbox_provider = exc.sandbox_provider
        task.sandbox_id = exc.sandbox_id
        error_type = (
            "artifact_contract_failed"
            if str(exc).startswith("artifact_contract_failed:")
            else "agent_execution_failed"
        )
        _fail_task(db, task, error_type, str(exc))
    except LearningDeckArtifactError as exc:
        db.rollback()
        _fail_task(db, task, "artifact_contract_failed", str(exc))
    except LlmTaskError:
        raise
    except Exception as exc:
        db.rollback()
        _fail_task(db, task, type(exc).__name__, str(exc))

    publish_state = _reload_publishable_state(
        db,
        task_id=task_id,
        deck_id=deck_id,
        lock_deck=True,
    )
    if publish_state is None:
        delete_learning_deck_objects(stored.artifact_object_keys)
        return _reload_task(db, task_id)
    task, deck = publish_state

    set_llm_task_status(
        db,
        task,
        status=LlmTaskStatus.COMPLETED,
        workflow_state=LlmWorkflowState.COMPLETED,
        note="Learning Deck is ready",
        output_json={
            "deck_id": deck_id,
            "deck_object_key": stored.deck_object_key,
            "source_notes_object_key": stored.source_notes_object_key,
            "source_notes_html_object_key": stored.source_notes_html_object_key,
            "artifact_object_keys": stored.artifact_object_keys,
        },
        artifact_manifest={
            "artifact_storage_prefix": stored.storage_prefix,
            "deck_object_key": stored.deck_object_key,
            "source_notes_object_key": stored.source_notes_object_key,
            "source_notes_html_object_key": stored.source_notes_html_object_key,
            "artifact_object_keys": stored.artifact_object_keys,
        },
    )
    commit_learning_deck_artifact_promotion(
        db,
        deck,
        artifact=stored,
        latest_task_id=task_id,
        source_metadata=result.source_metadata_updates,
    )
    return task


def _set_status(
    db: Session,
    task: LlmTask,
    deck: LearningDeck,
    status: LlmTaskStatus,
    note: str,
) -> None:
    deck.latest_task_id = task.id
    deck.updated_at = utcnow()
    state = (
        LlmWorkflowState.RUNNING if status == LlmTaskStatus.RUNNING else LlmWorkflowState.PREPARING
    )
    set_llm_task_status(db, task, status=status, workflow_state=state, note=note)
    db.commit()


def _fail_task(db: Session, task: LlmTask, error_type: str, message: str) -> NoReturn:
    set_llm_task_status(
        db,
        task,
        status=LlmTaskStatus.FAILED,
        workflow_state=LlmWorkflowState.FAILED,
        note="Learning Deck generation failed",
        error_type=error_type,
        error_message=message[:4000],
    )
    db.commit()
    raise LlmTaskError(message)


def _cancel_task(db: Session, task: LlmTask, message: str) -> LlmTask:
    if task.status not in TERMINAL_LLM_TASK_STATUSES:
        set_llm_task_status(
            db,
            task,
            status=LlmTaskStatus.CANCELLED,
            workflow_state=LlmWorkflowState.CANCELLED,
            note=message,
            error_type="deck_deleted",
            error_message=message,
        )
        db.commit()
    return task


def _reload_publishable_state(
    db: Session,
    *,
    task_id: int,
    deck_id: int,
    lock_deck: bool = False,
) -> tuple[LlmTask, LearningDeck] | None:
    deck_query = db.query(LearningDeck).filter(LearningDeck.id == deck_id)
    if lock_deck:
        deck_query = deck_query.with_for_update()
    deck = deck_query.populate_existing().first()
    task = db.query(LlmTask).filter(LlmTask.id == task_id).populate_existing().first()
    if task is None:
        raise LlmTaskError("Learning Deck LLM task not found")
    if task.status in TERMINAL_LLM_TASK_STATUSES:
        return None
    if deck is None:
        _fail_task(db, task, "deck_not_found", "Learning Deck not found")
    if deck.deleted_at is not None:
        _cancel_task(db, task, "Learning Deck was deleted")
        return None
    return task, deck


def _reload_task(db: Session, task_id: int) -> LlmTask:
    task = db.query(LlmTask).filter(LlmTask.id == task_id).populate_existing().first()
    if task is None:
        raise LlmTaskError("Learning Deck LLM task not found")
    return task


def _build_source_snapshot(db: Session, deck: LearningDeck) -> dict[str, Any]:
    if deck.source_kind == LearningDeckSourceKind.CONTENT.value:
        return build_content_source_snapshot_for_deck(db, deck=deck)
    if deck.source_kind == LearningDeckSourceKind.GITHUB_REPO.value:
        return build_github_source_snapshot_for_deck(deck)
    raise LlmTaskError(f"Unsupported Learning Deck source kind: {deck.source_kind}")


def _source_terminal_error(
    db: Session,
    deck: LearningDeck,
) -> tuple[str, str] | None:
    if not deck.source_content_id:
        return None
    content_id = int(deck.source_content_id)
    content = db.query(Content).filter(Content.id == content_id).first()
    if content is None:
        return "source_not_found", "Source content no longer exists"
    if content.status in {ContentStatus.FAILED.value, ContentStatus.SKIPPED.value}:
        return "source_processing_failed", "Source content processing failed"

    active_source_task_id = (
        db.query(ProcessingTask.id)
        .filter(ProcessingTask.content_id == content_id)
        .filter(ProcessingTask.task_type.in_(SOURCE_PREPARATION_TASK_TYPES))
        .filter(
            ProcessingTask.status.in_([TaskStatus.PENDING.value, TaskStatus.PROCESSING.value])
        )
        .limit(1)
        .scalar()
    )
    if active_source_task_id is not None:
        return None

    latest_source_task = (
        db.query(ProcessingTask.status, ProcessingTask.error_message)
        .filter(ProcessingTask.content_id == content_id)
        .filter(ProcessingTask.task_type.in_(SOURCE_PREPARATION_TASK_TYPES))
        .order_by(ProcessingTask.id.desc())
        .first()
    )
    if latest_source_task is not None and latest_source_task.status == TaskStatus.FAILED.value:
        return (
            "source_processing_failed",
            latest_source_task.error_message or "Source content processing failed",
        )
    if content.status in {ContentStatus.COMPLETED.value, ContentStatus.AWAITING_IMAGE.value}:
        return "source_text_unavailable", "Source content completed without readable source text"
    return (
        "source_pipeline_stalled",
        "Source content is not ready and has no active preparation task",
    )


def _source_wait_delay_seconds(task: LlmTask) -> int:
    created_at = task.created_at or utcnow()
    age_seconds = max(0, int((utcnow() - created_at).total_seconds()))
    return min(300, max(30, 30 * (2 ** min(age_seconds // 300, 4))))


def _store_agent_log(
    db: Session,
    task: LlmTask,
    deck: LearningDeck,
    events: list[dict[str, Any]],
) -> None:
    if not events:
        return
    try:
        key = store_learning_deck_agent_log(
            user_id=require_int_value(task.user_id, "LLM task user id"),
            deck_id=require_int_value(deck.id, "Learning Deck id"),
            run_id=require_llm_task_id(task),
            events=events,
        )
    except Exception:
        return
    task.agent_log_object_key = key
    db.commit()


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _persistable_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if key != "body_text"}
