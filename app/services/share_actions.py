"""Workflow orchestration for VM-backed ShareSheet actions."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.commands import ingest_content as ingest_content_command
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
    TaskStatus,
    TaskType,
)
from app.models.db import LlmTask, LlmTaskAction, ProcessingTask, User
from app.pipeline.task_specs import get_task_spec
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
from app.services.share_action_agent import (
    ShareActionAgentExecutionError,
    ShareActionAgentRunResult,
    run_share_action_agent,
)
from app.services.share_action_workflows import (
    AddLinksActionInput,
    ContentActionInput,
    FeedActionInput,
    LearningDeckActionInput,
    ShareActionInput,
    allowed_share_actions,
    build_share_action_request,
    parse_share_action_input,
)

ShareActionAgentRunner = Callable[[Session, LlmTask], ShareActionAgentRunResult]

DEFAULT_SHARE_ACTION_APPROVAL_POLICY = {"default": LlmTaskApprovalPolicy.AUTO_APPLY.value}


def create_share_action(
    db: Session,
    *,
    current_user: User,
    payload: ShareActionCreateRequest,
) -> ShareActionResponse:
    """Create a Share Action LLM task and enqueue it for processing."""
    user_id = _require_user_id(current_user)
    mode = payload.mode
    allowed_actions = allowed_share_actions(mode)

    llm_task = create_llm_task(
        db,
        user_id=user_id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=mode,
        workflow_key=f"share_action.{mode.value}.v1",
        approval_policy=_approval_policy_json(payload.approval_policy),
        allowed_actions=allowed_actions,
        tool_policy={"execute_bash": True, "web_search": True, "files": "read_write"},
        prompt_pack=f"share_action.{mode.value}",
        input_json={
            "url": payload.url,
            "mode": mode.value,
            "instruction": payload.instruction,
            "chat_initial_message": payload.chat_initial_message,
            "save_to_knowledge_and_mark_read": (
                payload.save_to_knowledge_and_mark_read or mode == LlmTaskMode.CHAT
            ),
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
    """Run one Share Action LLM task from VM agent through host action application."""
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
        note="Preparing Share Action VM workspace",
    )
    db.commit()
    set_llm_task_status(
        db,
        task,
        status=LlmTaskStatus.RUNNING,
        workflow_state=LlmWorkflowState.RUNNING,
        note="Running Share Action agent",
    )
    db.commit()

    try:
        runner = agent_runner or _run_default_agent
        agent_result = runner(db, task)
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
    action.action_status = LlmTaskActionStatus.APPLYING.value
    action.started_at = utcnow()
    db.flush()
    try:
        result = _apply_action(db, task=task, action=action)
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
        mark_llm_task_action_failed(db, action=action, error_message=str(exc))
        raise


def _run_default_agent(_db: Session, task: LlmTask) -> ShareActionAgentRunResult:
    return run_share_action_agent(task=task)


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
    _task: LlmTask,
    user: User,
    action_input: ShareActionInput,
) -> dict[str, Any]:
    if not isinstance(action_input, ContentActionInput):
        raise LlmTaskError("add_content action input has the wrong schema")
    result = _submit_content(db, user=user, action_input=action_input)
    return {"content_id": result.content_id, "task_id": result.job_id}


def _apply_save_to_knowledge_action(
    db: Session,
    _task: LlmTask,
    user: User,
    action_input: ShareActionInput,
) -> dict[str, Any]:
    if not isinstance(action_input, ContentActionInput):
        raise LlmTaskError("save_to_knowledge action input has the wrong schema")
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


def _apply_enqueue_chat_action(
    db: Session,
    _task: LlmTask,
    user: User,
    action_input: ShareActionInput,
) -> dict[str, Any]:
    if not isinstance(action_input, ContentActionInput):
        raise LlmTaskError("enqueue_chat action input has the wrong schema")
    result = _submit_content(
        db,
        user=user,
        action_input=action_input,
        share_and_chat=True,
        save_to_knowledge_and_mark_read=True,
    )
    return {"content_id": result.content_id, "task_id": result.job_id}


def _apply_create_learning_deck_action(
    db: Session,
    _task: LlmTask,
    user: User,
    action_input: ShareActionInput,
) -> dict[str, Any]:
    if not isinstance(action_input, LearningDeckActionInput):
        raise LlmTaskError("create_learning_deck action input has the wrong schema")
    deck = create_or_rerun_learning_deck(
        db,
        current_user=user,
        url=action_input.source_url,
        interests_prompt=_clean_optional_text(action_input.interests_prompt),
    )
    return {"learning_deck_id": deck.id, "source_url": action_input.source_url}


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
    save_to_knowledge_and_mark_read = bool(
        action_input.save_to_knowledge_and_mark_read
    ) or _input_save_to_knowledge_and_mark_read(task)
    applied: list[dict[str, Any]] = []
    for candidate in action_input.content_urls[:20]:
        try:
            result = _submit_content(
                db,
                user=user,
                action_input=candidate,
                save_to_knowledge_and_mark_read=save_to_knowledge_and_mark_read,
            )
        except Exception as exc:  # noqa: BLE001
            applied.append({"url": candidate.url, "error": str(exc)})
            continue
        applied.append(
            {
                "url": candidate.url,
                "content_id": result.content_id,
                "task_id": result.job_id,
            }
        )
    return {"items": applied}


_SHARE_ACTION_APPLICATORS: dict[
    str,
    Callable[[Session, LlmTask, User, ShareActionInput], dict[str, Any]],
] = {
    "add_content": _apply_add_content_action,
    "save_to_knowledge": _apply_save_to_knowledge_action,
    "subscribe_to_feed": _apply_subscribe_to_feed_action,
    "enqueue_chat": _apply_enqueue_chat_action,
    "add_links": _apply_add_links,
    "create_learning_deck": _apply_create_learning_deck_action,
}


def _enqueue_llm_task(db: Session, *, llm_task_id: int, user_id: int) -> int:
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
        raise LlmTaskError("Share Action queue task was not created")
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


def _input_save_to_knowledge_and_mark_read(task: LlmTask) -> bool:
    input_json = task.input_json if isinstance(task.input_json, dict) else {}
    return bool(input_json.get("save_to_knowledge_and_mark_read"))


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
