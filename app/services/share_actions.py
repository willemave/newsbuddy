"""Workflow orchestration for VM-backed ShareSheet actions."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from app.commands import ingest_content as ingest_content_command
from app.core.logging import get_logger
from app.models.api.share_actions import (
    ShareActionAgentResult,
    ShareActionCreateRequest,
    ShareActionResponse,
)
from app.models.api.submissions import SubmitContentRequest
from app.models.contracts import (
    ContentType,
    LlmTaskActionStatus,
    LlmTaskApprovalPolicy,
    LlmTaskKind,
    LlmTaskMode,
    LlmTaskStatus,
    LlmWorkflowState,
    TaskType,
)
from app.models.db import Content, LlmTask, LlmTaskAction, User
from app.services.content_submission import normalize_url
from app.services.dig_deeper import get_or_create_dig_deeper_session
from app.services.gateways.task_queue_gateway import get_task_queue_gateway
from app.services.learning_decks import create_or_rerun_learning_deck
from app.services.llm_tasks import (
    LlmTaskError,
    create_llm_task,
    list_llm_task_actions,
    mark_llm_task_action_applied,
    mark_llm_task_action_failed,
    present_llm_task_action,
    request_llm_task_action,
    require_llm_task_id,
    set_llm_task_status,
    utcnow,
)
from app.services.queue import TaskEnqueueRequest
from app.services.share_action_agent import (
    ShareActionAgentExecutionError,
    ShareActionAgentRunResult,
    run_share_action_agent,
)
from app.services.share_action_workflows import (
    AddLinksActionInput,
    AddToBriefingActionInput,
    ContentActionInput,
    FeedActionInput,
    LearningDeckActionInput,
    ShareActionInput,
    ShareActionWorkflowSpec,
    allowed_share_actions,
    build_share_action_request,
    parse_share_action_input,
    share_action_idempotency_key,
    share_action_workflow_for_mode,
)

ShareActionAgentRunner = Callable[[Session, LlmTask], ShareActionAgentRunResult]

DEFAULT_SHARE_ACTION_APPROVAL_POLICY = {"default": LlmTaskApprovalPolicy.AUTO_APPLY.value}
logger = get_logger(__name__)


class ShareActionApplyOutcome(StrEnum):
    """Stable internal outcomes persisted in Share Action result JSON."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    SUBMITTED = "submitted"


def create_share_action(
    db: Session,
    *,
    current_user: User,
    payload: ShareActionCreateRequest,
) -> ShareActionResponse:
    """Create a Share Action LLM task and enqueue it for processing."""
    user_id = _require_user_id(current_user)
    mode = payload.mode

    llm_task = create_llm_task(
        db,
        user_id=user_id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=mode,
        workflow_key=f"share_action.{mode.value}.v1",
        approval_policy=_approval_policy_json(payload.approval_policy),
        allowed_actions=allowed_share_actions(mode),
        tool_policy={"execute_bash": True, "web_search": True, "files": "read_write"},
        prompt_pack=f"share_action.{mode.value}",
        input_json={
            "url": payload.url,
            "mode": mode.value,
            "instruction": payload.instruction,
            "chat_initial_message": payload.chat_initial_message,
            "interests_prompt": payload.interests_prompt,
        },
    )
    llm_task_id = require_llm_task_id(llm_task)
    _enqueue_llm_task(db, llm_task_id=llm_task_id, user_id=user_id)
    db.commit()
    db.refresh(llm_task)
    return present_share_action(db, llm_task)


def present_share_action(db: Session, task: LlmTask) -> ShareActionResponse:
    """Build a Share Action API response from a generic LLM task."""
    if task.created_at is None:
        raise LlmTaskError("Share Action task is missing created_at")
    return ShareActionResponse(
        task_id=require_llm_task_id(task),
        mode=LlmTaskMode(str(task.mode)),
        status=LlmTaskStatus(str(task.status)),
        workflow_state=str(task.workflow_state),
        created_at=task.created_at,
        actions=[
            present_llm_task_action(action) for action in list_llm_task_actions(db, task=task)
        ],
    )


def run_share_action_task(
    db: Session,
    *,
    llm_task_id: int,
    agent_runner: ShareActionAgentRunner | None = None,
) -> LlmTask:
    """Run one Share Action workflow through validated host action application."""
    task = db.query(LlmTask).filter(LlmTask.id == llm_task_id).first()
    if task is None:
        raise LlmTaskError("LLM task not found")
    if task.task_kind != LlmTaskKind.SHARE_ACTION.value:
        raise LlmTaskError(f"LLM task is not a Share Action: {task.task_kind}")
    if task.status == LlmTaskStatus.COMPLETED.value:
        return task

    set_llm_task_status(
        db,
        task,
        status=LlmTaskStatus.PREPARING,
        workflow_state=LlmWorkflowState.PREPARING,
        note="Preparing Share Action workflow",
    )
    db.commit()

    try:
        user = db.query(User).filter(User.id == task.user_id).first()
        if user is None:
            raise LlmTaskError("Share Action user not found")
        workflow = share_action_workflow_for_mode(LlmTaskMode(str(task.mode)))
        _prepare_share_action_source(db, task=task, user=user, workflow=workflow)
        set_llm_task_status(
            db,
            task,
            status=LlmTaskStatus.RUNNING,
            workflow_state=LlmWorkflowState.RUNNING,
            note=(
                "Running deterministic chat handoff"
                if task.mode == LlmTaskMode.CHAT.value and agent_runner is None
                else "Running Share Action agent"
            ),
        )
        db.commit()
        if task.mode == LlmTaskMode.CHAT.value and agent_runner is None:
            set_llm_task_status(
                db,
                task,
                status=LlmTaskStatus.APPLYING,
                workflow_state=LlmWorkflowState.APPLYING,
                note="Applying deterministic chat handoff",
            )
            db.commit()
            _ensure_host_chat_action(db, task=task)
        else:
            agent_result = (agent_runner or _run_default_agent)(db, task)
            set_llm_task_status(
                db,
                task,
                status=LlmTaskStatus.APPLYING,
                workflow_state=LlmWorkflowState.APPLYING,
                note="Applying Share Action result",
                output_json=agent_result.result.model_dump(mode="json"),
                model_provider=agent_result.model_provider,
                model_name=agent_result.model_name,
                sandbox_provider=agent_result.sandbox_provider,
                sandbox_id=agent_result.sandbox_id,
            )
            db.commit()
            _ensure_result_action(db, task=task, result=agent_result.result)
        _apply_auto_approved_actions(db, task=task)
        if _has_pending_approval_actions(db, task=task):
            set_llm_task_status(
                db,
                task,
                status=LlmTaskStatus.AWAITING_APPROVAL,
                workflow_state=LlmWorkflowState.AWAITING_APPROVAL,
                note="Awaiting Share Action approval",
            )
        else:
            set_llm_task_status(
                db,
                task,
                status=LlmTaskStatus.COMPLETED,
                workflow_state=LlmWorkflowState.COMPLETED,
                note="Share Action completed",
            )
        db.commit()
    except ShareActionAgentExecutionError as exc:
        db.rollback()
        set_llm_task_status(
            db,
            task,
            status=LlmTaskStatus.FAILED,
            workflow_state=LlmWorkflowState.FAILED,
            note="Share Action agent failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
            sandbox_provider=exc.sandbox_provider,
            sandbox_id=exc.sandbox_id,
        )
        db.commit()
        raise
    except Exception as exc:
        db.rollback()
        set_llm_task_status(
            db,
            task,
            status=LlmTaskStatus.FAILED,
            workflow_state=LlmWorkflowState.FAILED,
            note="Share Action failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        db.commit()
        raise
    return task


def apply_share_task_action(db: Session, *, task: LlmTask, action: LlmTaskAction) -> None:
    """Apply one approved Share Action callback through existing Newsly services."""
    if task.task_kind != LlmTaskKind.SHARE_ACTION.value:
        return
    if action.action_status != LlmTaskActionStatus.APPROVED.value:
        return
    task_id = require_llm_task_id(task)
    action_id = action.id
    if action_id is None:
        raise LlmTaskError("Share Action callback is missing an id")
    approved_by_user_id = action.approved_by_user_id
    approved_at = action.approved_at
    action.action_status = LlmTaskActionStatus.APPLYING.value
    action.started_at = utcnow()
    started_at = action.started_at
    failed_result: dict[str, Any] | None = None
    db.flush()
    try:
        result = _apply_action(db, task=task, action=action)
        if result.get("outcome") == ShareActionApplyOutcome.FAILED:
            failed_result = result
            raise LlmTaskError(result.get("error") or "Share Action applied no items")
        mark_llm_task_action_applied(db, action=action, action_result=result)
        if not _has_pending_approval_actions(db, task=task):
            set_llm_task_status(
                db,
                task,
                status=LlmTaskStatus.COMPLETED,
                workflow_state=LlmWorkflowState.COMPLETED,
                note="Approved Share Action applied",
            )
    except Exception as exc:
        try:
            # Applicators can fail during flush or commit, leaving the session
            # unusable until rollback. Reload both durable rows before recording
            # the terminal callback state.
            db.rollback()
            persisted_task = db.query(LlmTask).filter(LlmTask.id == task_id).one()
            persisted_action = db.query(LlmTaskAction).filter(LlmTaskAction.id == action_id).one()
            persisted_action.approved_by_user_id = approved_by_user_id
            persisted_action.approved_at = approved_at
            persisted_action.started_at = started_at
            if failed_result is not None:
                persisted_action.action_result = failed_result
            mark_llm_task_action_failed(db, action=persisted_action, error_message=str(exc))
            set_llm_task_status(
                db,
                persisted_task,
                status=LlmTaskStatus.FAILED,
                workflow_state=LlmWorkflowState.FAILED,
                note="Share Action application failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            # Approval callbacks run outside the task-level orchestrator, so
            # persist both terminal rows before returning the application error.
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception(
                "Could not persist failed Share Action application state",
                extra={
                    "component": "share_action",
                    "operation": "persist_application_failure",
                    "task_id": task_id,
                    "context_data": {"action_id": action_id},
                },
            )
        raise


def _run_default_agent(_db: Session, task: LlmTask) -> ShareActionAgentRunResult:
    return run_share_action_agent(task=task)


def _prepare_share_action_source(
    db: Session,
    *,
    task: LlmTask,
    user: User,
    workflow: ShareActionWorkflowSpec,
) -> None:
    """Persist the shared source once before agent or host action execution."""
    if not workflow.save_shared_source_to_knowledge or _input_knowledge_content_id(task):
        return

    input_json = dict(task.input_json) if isinstance(task.input_json, dict) else {}
    url = _clean_optional_text(input_json.get("url"))
    if url is None:
        raise LlmTaskError("Share Action URL is missing")
    initial_message = _clean_optional_text(input_json.get("chat_initial_message"))
    result = _submit_content(
        db,
        user=user,
        action_input=ContentActionInput(
            url=url,
            instruction=_clean_optional_text(input_json.get("instruction")),
            chat_initial_message=initial_message,
        ),
        share_and_chat=workflow.share_and_chat,
        save_to_knowledge_and_mark_read=workflow.save_shared_source_to_knowledge,
    )
    input_json["knowledge_content_id"] = result.content_id
    input_json["knowledge_task_id"] = result.job_id
    task.input_json = input_json
    db.commit()


def _ensure_host_chat_action(db: Session, *, task: LlmTask) -> None:
    """Dispatch the deterministic chat handoff directly to the host action ledger."""
    input_json = task.input_json if isinstance(task.input_json, dict) else {}
    url = _clean_optional_text(input_json.get("url"))
    if url is None:
        raise LlmTaskError("Share Action chat URL is missing")
    workflow = share_action_workflow_for_mode(LlmTaskMode.CHAT)
    action_input = ContentActionInput(
        url=url,
        chat_initial_message=_clean_optional_text(input_json.get("chat_initial_message")),
    ).model_dump(mode="json", exclude_none=True)
    request_llm_task_action(
        db,
        task=task,
        action_name=workflow.host_action_name,
        action_input=action_input,
        rationale="Use the canonical content pipeline before starting chat",
        idempotency_key=share_action_idempotency_key(workflow.host_action_name, action_input),
    )
    db.commit()


def _approval_policy_json(
    approval_policy: dict[str, LlmTaskApprovalPolicy] | None,
) -> dict[str, str]:
    if approval_policy is None:
        return dict(DEFAULT_SHARE_ACTION_APPROVAL_POLICY)
    return {key: value.value for key, value in approval_policy.items()}


def _ensure_result_action(db: Session, *, task: LlmTask, result: ShareActionAgentResult) -> None:
    action_request = build_share_action_request(task=task, result=result)
    if action_request is None:
        return
    request_llm_task_action(
        db,
        task=task,
        action_name=action_request.action_name,
        action_input=action_request.action_input,
        rationale=result.rationale,
        idempotency_key=action_request.idempotency_key,
    )
    db.commit()


def _apply_auto_approved_actions(db: Session, *, task: LlmTask) -> None:
    actions = list_llm_task_actions(db, task=task)
    for action in actions:
        if action.action_status == LlmTaskActionStatus.APPROVED.value:
            apply_share_task_action(db, task=task, action=action)
            db.commit()


def _has_pending_approval_actions(db: Session, *, task: LlmTask) -> bool:
    actions = list_llm_task_actions(db, task=task)
    return any(
        action.action_status
        in {
            LlmTaskActionStatus.AWAITING_APPROVAL.value,
            LlmTaskActionStatus.PROPOSED.value,
        }
        for action in actions
    )


def _apply_action(db: Session, *, task: LlmTask, action: LlmTaskAction) -> dict[str, Any]:
    user = db.query(User).filter(User.id == task.user_id).first()
    if user is None:
        raise LlmTaskError("Share Action user not found")
    typed_input = parse_share_action_input(
        task=task,
        action_name=str(action.action_name),
        action_input=action.action_input,
    )
    applicator = _SHARE_ACTION_APPLICATORS.get(str(action.action_name))
    if applicator is None:
        raise LlmTaskError(f"Unsupported Share Action callback: {action.action_name}")
    return applicator(db, task, user, typed_input)


def _apply_add_content_action(
    db: Session,
    task: LlmTask,
    user: User,
    action_input: ShareActionInput,
) -> dict[str, Any]:
    if not isinstance(action_input, ContentActionInput):
        raise LlmTaskError("add_content action input has the wrong schema")
    prepared = _prepared_content_for_url(db, task=task, url=action_input.url)
    if prepared is not None:
        _enrich_prepared_content(prepared, action_input)
        return _prepared_content_result(task, prepared)
    result = _submit_content(
        db,
        user=user,
        action_input=action_input,
        save_to_knowledge_and_mark_read=True,
    )
    return {"content_id": result.content_id, "task_id": result.job_id}


def _apply_save_to_knowledge_action(
    db: Session,
    task: LlmTask,
    user: User,
    action_input: ShareActionInput,
) -> dict[str, Any]:
    if not isinstance(action_input, ContentActionInput):
        raise LlmTaskError("save_to_knowledge action input has the wrong schema")
    prepared = _prepared_content_for_url(db, task=task, url=action_input.url)
    if prepared is not None:
        _enrich_prepared_content(prepared, action_input)
        return _prepared_content_result(task, prepared)
    result = _submit_content(
        db,
        user=user,
        action_input=action_input,
        save_to_knowledge_and_mark_read=True,
    )
    return {"content_id": result.content_id, "task_id": result.job_id}


def _apply_subscribe_to_feed_action(
    db: Session,
    _task: LlmTask,
    user: User,
    action_input: ShareActionInput,
) -> dict[str, Any]:
    if not isinstance(action_input, FeedActionInput):
        raise LlmTaskError("subscribe_to_feed action input has the wrong schema")
    result = _submit_content(db, user=user, action_input=action_input, subscribe_to_feed=True)
    return {"content_id": result.content_id, "task_id": result.job_id}


def _apply_add_to_briefing_action(
    db: Session,
    _task: LlmTask,
    user: User,
    action_input: ShareActionInput,
) -> dict[str, Any]:
    if not isinstance(action_input, AddToBriefingActionInput):
        raise LlmTaskError("add_to_briefing action input has the wrong schema")
    target = action_input.root
    if target.kind == "feed":
        result = _submit_content(
            db,
            user=user,
            action_input=FeedActionInput(
                url=target.url,
                title=target.title,
                platform=target.platform,
                instruction=target.rationale,
            ),
            subscribe_to_feed=True,
        )
    else:
        result = _submit_content(
            db,
            user=user,
            action_input=ContentActionInput(
                url=target.url,
                title=target.title,
                platform=target.platform,
                content_type=target.content_type,
                instruction=target.rationale,
            ),
        )
    return {
        "resolved_kind": target.kind,
        "resolved_url": target.url,
        "content_id": result.content_id,
        "task_id": result.job_id,
        "already_exists": result.response.already_exists,
    }


def _apply_enqueue_chat_action(
    db: Session,
    task: LlmTask,
    user: User,
    action_input: ShareActionInput,
) -> dict[str, Any]:
    if not isinstance(action_input, ContentActionInput):
        raise LlmTaskError("enqueue_chat action input has the wrong schema")
    content = _prepared_content_for_url(db, task=task, url=action_input.url)
    task_id = _input_knowledge_task_id(task)
    if content is None:
        result = _submit_content(
            db,
            user=user,
            action_input=action_input,
            share_and_chat=True,
            save_to_knowledge_and_mark_read=True,
        )
        content = db.query(Content).filter(Content.id == result.content_id).one()
        task_id = result.job_id
    else:
        _enrich_prepared_content(content, action_input)
    session = get_or_create_dig_deeper_session(db, content, _require_user_id(user))
    if session.id is None:
        raise LlmTaskError("Share Action chat session was not created")
    return {
        "content_id": _require_content_id(content),
        "task_id": task_id,
        "chat_session_id": int(session.id),
    }


def _apply_create_learning_deck_action(
    db: Session,
    task: LlmTask,
    user: User,
    action_input: ShareActionInput,
) -> dict[str, Any]:
    if not isinstance(action_input, LearningDeckActionInput):
        raise LlmTaskError("create_learning_deck action input has the wrong schema")
    content_id = _input_knowledge_content_id(task)
    if content_id is None:
        content_result = _submit_content(
            db,
            user=user,
            action_input=ContentActionInput(
                url=action_input.source_url,
                title=action_input.title,
            ),
            save_to_knowledge_and_mark_read=True,
        )
        content_id = content_result.content_id
    deck = create_or_rerun_learning_deck(
        db,
        current_user=user,
        url=action_input.source_url,
        interests_prompt=_clean_optional_text(action_input.interests_prompt),
        submitted_via="share_action",
        share_action_task_id=require_llm_task_id(task),
    )
    return {
        "learning_deck_id": deck.id,
        "source_url": action_input.source_url,
        "content_id": content_id,
    }


def _prepared_content_for_url(db: Session, *, task: LlmTask, url: str) -> Content | None:
    content_id = _input_knowledge_content_id(task)
    input_json = task.input_json if isinstance(task.input_json, dict) else {}
    source_url = _clean_optional_text(input_json.get("url"))
    if content_id is None or source_url is None:
        return None
    if normalize_url(source_url) != normalize_url(url):
        return None
    return db.query(Content).filter(Content.id == content_id).first()


def _enrich_prepared_content(content: Content, action_input: ContentActionInput) -> None:
    if action_input.title and not content.title:
        content.title = action_input.title
    if action_input.platform and not content.platform:
        content.platform = action_input.platform


def _prepared_content_result(task: LlmTask, content: Content) -> dict[str, Any]:
    return {
        "content_id": _require_content_id(content),
        "task_id": _input_knowledge_task_id(task),
    }


def _submit_content(
    db: Session,
    *,
    user: User,
    action_input: ContentActionInput | FeedActionInput,
    subscribe_to_feed: bool = False,
    share_and_chat: bool = False,
    save_to_knowledge_and_mark_read: bool = False,
):
    content_type_value = (
        action_input.content_type if isinstance(action_input, ContentActionInput) else None
    )
    payload = SubmitContentRequest(
        url=action_input.url,
        content_type=_content_type(content_type_value),
        title=_clean_optional_text(action_input.title),
        platform=_clean_optional_text(action_input.platform),
        instruction=_clean_optional_text(action_input.instruction),
        crawl_links=False,
        subscribe_to_feed=subscribe_to_feed,
        share_and_chat=share_and_chat,
        chat_initial_message=_clean_optional_text(
            action_input.chat_initial_message
            if isinstance(action_input, ContentActionInput)
            else None
        ),
        save_to_knowledge_and_mark_read=save_to_knowledge_and_mark_read,
    )
    return ingest_content_command.execute(
        db,
        payload=payload,
        current_user=user,
        submitted_via="share_action",
    )


def _apply_add_links(
    db: Session,
    task: LlmTask,
    user: User,
    action_input: ShareActionInput,
) -> dict[str, Any]:
    if not isinstance(action_input, AddLinksActionInput):
        raise LlmTaskError("add_links action input has the wrong schema")
    workflow = share_action_workflow_for_mode(LlmTaskMode(str(task.mode)))
    applied: list[dict[str, Any]] = []
    for candidate in action_input.content_urls[:20]:
        try:
            submission_result = _submit_content(
                db,
                user=user,
                action_input=candidate,
                save_to_knowledge_and_mark_read=workflow.save_shared_source_to_knowledge,
            )
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            applied.append(
                {
                    "url": candidate.url,
                    "outcome": ShareActionApplyOutcome.FAILED,
                    "error": str(exc),
                }
            )
            continue
        applied.append(
            {
                "url": candidate.url,
                "outcome": ShareActionApplyOutcome.SUBMITTED,
                "content_id": submission_result.content_id,
                "task_id": submission_result.job_id,
            }
        )
    succeeded_count = sum(item["outcome"] == ShareActionApplyOutcome.SUBMITTED for item in applied)
    failed_count = len(applied) - succeeded_count
    if failed_count == 0:
        outcome = ShareActionApplyOutcome.COMPLETED
    elif succeeded_count > 0:
        outcome = ShareActionApplyOutcome.PARTIAL
    else:
        outcome = ShareActionApplyOutcome.FAILED
    result: dict[str, Any] = {
        "outcome": outcome,
        "attempted_count": len(applied),
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
        "items": applied,
    }
    if outcome == ShareActionApplyOutcome.FAILED:
        result["error"] = "All discovered links failed to submit"
    return result


_SHARE_ACTION_APPLICATORS: dict[
    str,
    Callable[[Session, LlmTask, User, ShareActionInput], dict[str, Any]],
] = {
    "add_content": _apply_add_content_action,
    "save_to_knowledge": _apply_save_to_knowledge_action,
    "subscribe_to_feed": _apply_subscribe_to_feed_action,
    "add_to_briefing": _apply_add_to_briefing_action,
    "enqueue_chat": _apply_enqueue_chat_action,
    "add_links": _apply_add_links,
    "create_learning_deck": _apply_create_learning_deck_action,
}


def _enqueue_llm_task(db: Session, *, llm_task_id: int, user_id: int) -> int:
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


def _input_knowledge_content_id(task: LlmTask) -> int | None:
    input_json = task.input_json if isinstance(task.input_json, dict) else {}
    value = input_json.get("knowledge_content_id")
    return int(value) if isinstance(value, int) else None


def _input_knowledge_task_id(task: LlmTask) -> int | None:
    input_json = task.input_json if isinstance(task.input_json, dict) else {}
    value = input_json.get("knowledge_task_id")
    return int(value) if isinstance(value, int) else None


def _clean_optional_text(value: object) -> str | None:
    cleaned = value.strip() if isinstance(value, str) else ""
    return cleaned or None


def _content_type(value: object) -> ContentType | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return ContentType(value)
    except ValueError:
        return None


def _require_user_id(user: User) -> int:
    if user.id is None:
        raise LlmTaskError("User is missing an id")
    return int(user.id)


def _require_content_id(content: Content) -> int:
    if content.id is None:
        raise LlmTaskError("Share Action content is missing an id")
    return int(content.id)
