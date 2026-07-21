"""Generation orchestration for queued Learning Deck runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.contracts import (
    LearningDeckRunStatus,
    LearningDeckSourceKind,
    LlmTaskStatus,
    LlmWorkflowState,
)
from app.models.db import LearningDeck, LearningDeckRun, LlmTask
from app.services.learning_deck_agent import (
    LearningDeckAgentExecutionError,
    LearningDeckAgentResult,
    run_learning_deck_agent,
)
from app.services.learning_deck_artifacts import (
    LearningDeckArtifactError,
    render_source_notes_html,
    store_learning_deck_agent_log,
    store_learning_deck_artifact,
)
from app.services.learning_deck_common import (
    LearningDeckError,
    LearningDeckSourceNotReady,
    append_learning_deck_timeline,
    require_int_value,
    require_str_value,
    utcnow,
)
from app.services.learning_deck_sources import (
    build_content_source_snapshot,
    build_github_source_snapshot,
)
from app.services.learning_decks import (
    mark_learning_deck_run_failed,
    promote_learning_deck_run,
)
from app.services.llm_tasks import set_llm_task_status

logger = get_logger(__name__)

SOURCE_WAIT_RETRY_SECONDS = 300

LearningDeckAgentRunner = Callable[
    [dict[str, Any], str | None, int, int],
    LearningDeckAgentResult,
]


class LearningDeckGenerationWaiting(RuntimeError):
    """Raised when a run should retry after source preparation finishes."""

    def __init__(
        self,
        message: str,
        *,
        retry_delay_seconds: int = SOURCE_WAIT_RETRY_SECONDS,
    ) -> None:
        super().__init__(message)
        self.retry_delay_seconds = retry_delay_seconds


def generate_learning_deck(
    db: Session,
    *,
    learning_deck_run_id: int,
    agent_runner: LearningDeckAgentRunner | None = None,
) -> LearningDeckRun:
    """Run one Learning Deck generation attempt from source prep through promotion."""
    run = db.query(LearningDeckRun).filter(LearningDeckRun.id == learning_deck_run_id).first()
    if run is None:
        raise LearningDeckError("Learning Deck run not found", status_code=404)
    run_id = require_int_value(run.id, "Learning Deck run id")
    deck_id = require_int_value(run.deck_id, "Learning Deck id")
    user_id = require_int_value(run.user_id, "Learning Deck user id")
    deck = db.query(LearningDeck).filter(LearningDeck.id == deck_id).first()
    if deck is None or deck.deleted_at is not None:
        raise LearningDeckError("Learning Deck not found", status_code=404)
    if run.status == LearningDeckRunStatus.COMPLETED.value:
        _repair_completed_run_llm_task_status(db, run)
        return run

    _set_run_status(
        db,
        run,
        status="preparing",
        note="Preparing source material",
        started=True,
    )
    try:
        source_snapshot = _build_source_snapshot(db, run, deck)
    except LearningDeckSourceNotReady as exc:
        _set_run_status(db, run, status="preparing", note=str(exc))
        raise LearningDeckGenerationWaiting(str(exc)) from exc

    run.source_snapshot = _persistable_source_snapshot(source_snapshot)
    _set_run_status(db, run, status="generating", note="Running Learning Deck agent")

    try:
        if agent_runner is not None:
            agent_result = agent_runner(
                source_snapshot,
                run.interests_prompt,
                user_id,
                run_id,
            )
        else:
            agent_result = _run_default_agent(
                source_snapshot,
                run.interests_prompt,
                user_id,
                run_id,
                llm_task=_get_llm_task_for_run(db, run),
            )
        _store_agent_log_events(db, run, agent_result.agent_log_events)

        run.model_provider = agent_result.model_provider
        run.model_name = agent_result.model_name
        run.sandbox_provider = agent_result.sandbox_provider
        run.sandbox_id = agent_result.sandbox_id
        _set_llm_task_execution_metadata(
            db,
            run,
            model_provider=agent_result.model_provider,
            model_name=agent_result.model_name,
            sandbox_provider=agent_result.sandbox_provider,
            sandbox_id=agent_result.sandbox_id,
        )
        _merge_source_metadata(run, deck, agent_result.source_metadata_updates)
        _set_run_status(db, run, status="validating", note="Validating generated artifact")

        source_notes_html = render_source_notes_html(
            agent_result.source_notes_md,
            title=f"{deck.title} Source Notes",
        )
        _set_run_status(db, run, status="publishing", note="Publishing Learning Deck")
        stored = store_learning_deck_artifact(
            user_id=user_id,
            deck_id=deck_id,
            run_id=run_id,
            index_html=agent_result.index_html,
            source_notes_md=agent_result.source_notes_md,
            source_notes_html=source_notes_html,
            extra_assets=agent_result.assets,
        )
        promote_learning_deck_run(
            db,
            run,
            artifact=stored,
            source_metadata=agent_result.source_metadata_updates,
        )
    except LearningDeckArtifactError as exc:
        db.rollback()
        mark_learning_deck_run_failed(db, run, error_message=str(exc))
        raise
    except LearningDeckAgentExecutionError as exc:
        db.rollback()
        _store_agent_log_events(db, run, exc.agent_log_events)
        run.sandbox_provider = exc.sandbox_provider
        run.sandbox_id = exc.sandbox_id
        mark_learning_deck_run_failed(db, run, error_message=str(exc))
        raise
    except Exception as exc:
        db.rollback()
        logger.exception(
            "Learning Deck generation failed",
            extra={
                "component": "learning_decks",
                "operation": "generate",
                "status": "failed",
                "item_id": learning_deck_run_id,
                "user_id": user_id,
                "context_data": {"error": str(exc), "failure_class": type(exc).__name__},
            },
        )
        mark_learning_deck_run_failed(db, run, error_message=str(exc))
        raise

    return run


def _store_agent_log_events(
    db: Session,
    run: LearningDeckRun,
    events: list[dict[str, Any]],
) -> None:
    if not events:
        return
    run_id = require_int_value(run.id, "Learning Deck run id")
    deck_id = require_int_value(run.deck_id, "Learning Deck id")
    user_id = require_int_value(run.user_id, "Learning Deck user id")
    try:
        log_key = store_learning_deck_agent_log(
            user_id=user_id,
            deck_id=deck_id,
            run_id=run_id,
            events=events,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Unable to store Learning Deck internal agent log",
            extra={
                "component": "learning_decks",
                "operation": "store_agent_log",
                "status": "failed",
                "item_id": run_id,
                "user_id": user_id,
            },
        )
        return
    run.agent_log_object_key = log_key
    task = _get_llm_task_for_run(db, run)
    if task is not None:
        set_llm_task_status(
            db,
            task,
            status=LlmTaskStatus.RUNNING,
            workflow_state=LlmWorkflowState.RUNNING,
            note="Stored Learning Deck agent log",
            agent_log_object_key=log_key,
        )
    db.commit()


def _run_default_agent(
    source_snapshot: dict[str, Any],
    interests_prompt: str | None,
    user_id: int,
    run_id: int,
    *,
    llm_task: LlmTask | None = None,
) -> LearningDeckAgentResult:
    return run_learning_deck_agent(
        source_snapshot=source_snapshot,
        interests_prompt=interests_prompt,
        user_id=user_id,
        run_id=run_id,
        llm_task=llm_task,
    )


def _build_source_snapshot(
    db: Session,
    run: LearningDeckRun,
    deck: LearningDeck,
) -> dict[str, Any]:
    source_kind = require_str_value(deck.source_kind, "Learning Deck source kind")
    if source_kind == LearningDeckSourceKind.CONTENT.value:
        return build_content_source_snapshot(db, run=run)
    if source_kind == LearningDeckSourceKind.GITHUB_REPO.value:
        return build_github_source_snapshot(run)
    raise LearningDeckError(f"Unsupported Learning Deck source kind: {source_kind}")


def _set_run_status(
    db: Session,
    run: LearningDeckRun,
    *,
    status: str,
    note: str | None = None,
    started: bool = False,
) -> None:
    run.status = status
    if started and run.started_at is None:
        run.started_at = utcnow()
    if note:
        append_learning_deck_timeline(run, status=status, note=note)
    run_id = require_int_value(run.id, "Learning Deck run id")
    deck_id = require_int_value(run.deck_id, "Learning Deck id")
    deck = db.query(LearningDeck).filter(LearningDeck.id == deck_id).first()
    if deck is not None:
        deck.latest_run_id = run_id
        deck.updated_at = utcnow()
    _set_llm_task_for_run_status(db, run, status=status, note=note)
    db.commit()


def _set_llm_task_for_run_status(
    db: Session,
    run: LearningDeckRun,
    *,
    status: str,
    note: str | None,
) -> None:
    """Mirror Learning Deck intermediate status onto its generic LLM task."""
    mapped = _llm_status_for_learning_deck_status(status)
    if mapped is None:
        return
    task = _get_llm_task_for_run(db, run)
    if task is None:
        return
    set_llm_task_status(
        db,
        task,
        status=mapped[0],
        workflow_state=mapped[1],
        note=note,
    )


def _set_llm_task_execution_metadata(
    db: Session,
    run: LearningDeckRun,
    *,
    model_provider: str,
    model_name: str,
    sandbox_provider: str,
    sandbox_id: str | None,
) -> None:
    task = _get_llm_task_for_run(db, run)
    if task is None:
        return
    set_llm_task_status(
        db,
        task,
        status=LlmTaskStatus.RUNNING,
        workflow_state=LlmWorkflowState.RUNNING,
        note="Learning Deck agent completed",
        model_provider=model_provider,
        model_name=model_name,
        sandbox_provider=sandbox_provider,
        sandbox_id=sandbox_id,
    )


def _get_llm_task_for_run(db: Session, run: LearningDeckRun) -> LlmTask | None:
    if not run.llm_task_id:
        return None
    return db.query(LlmTask).filter(LlmTask.id == run.llm_task_id).first()


def _repair_completed_run_llm_task_status(db: Session, run: LearningDeckRun) -> None:
    """Keep the generic LLM task ledger consistent for idempotent completed runs."""
    task = _get_llm_task_for_run(db, run)
    if task is None:
        return
    run_id = require_int_value(run.id, "Learning Deck run id")
    deck_id = require_int_value(run.deck_id, "Learning Deck id")
    artifact_object_keys = [key for key in (run.artifact_object_keys or []) if isinstance(key, str)]
    output_json = {
        "learning_deck_run_id": run_id,
        "deck_id": deck_id,
        "deck_object_key": run.deck_object_key,
        "source_notes_object_key": run.source_notes_object_key,
        "source_notes_html_object_key": run.source_notes_html_object_key,
        "artifact_object_keys": artifact_object_keys,
    }
    if task.status == LlmTaskStatus.COMPLETED.value and task.output_json == output_json:
        return
    set_llm_task_status(
        db,
        task,
        status=LlmTaskStatus.COMPLETED,
        workflow_state=LlmWorkflowState.COMPLETED,
        note="Learning Deck is ready",
        output_json=output_json,
        artifact_manifest={
            "artifact_storage_prefix": run.artifact_storage_prefix,
            "deck_object_key": run.deck_object_key,
            "source_notes_object_key": run.source_notes_object_key,
            "source_notes_html_object_key": run.source_notes_html_object_key,
            "artifact_object_keys": artifact_object_keys,
        },
    )
    db.commit()


def _llm_status_for_learning_deck_status(
    status: str,
) -> tuple[LlmTaskStatus, LlmWorkflowState] | None:
    if status == "preparing":
        return LlmTaskStatus.PREPARING, LlmWorkflowState.PREPARING
    if status in {"generating", "validating"}:
        return LlmTaskStatus.RUNNING, LlmWorkflowState.RUNNING
    if status == "publishing":
        return LlmTaskStatus.APPLYING, LlmWorkflowState.APPLYING
    return None


def _persistable_source_snapshot(source_snapshot: dict[str, Any]) -> dict[str, Any]:
    persisted = dict(source_snapshot)
    body_text = persisted.pop("body_text", None)
    if isinstance(body_text, str):
        persisted["body_text_chars"] = len(body_text)
    return persisted


def _merge_source_metadata(
    run: LearningDeckRun,
    deck: LearningDeck,
    source_metadata_updates: dict[str, Any],
) -> None:
    if not source_metadata_updates:
        return
    run_snapshot = run.source_snapshot if isinstance(run.source_snapshot, dict) else {}
    run.source_snapshot = {
        **run_snapshot,
        "source_metadata": {
            **(run_snapshot.get("source_metadata") or {}),
            **source_metadata_updates,
        },
    }
    deck_metadata = deck.source_metadata if isinstance(deck.source_metadata, dict) else {}
    deck.source_metadata = {**deck_metadata, **source_metadata_updates}
