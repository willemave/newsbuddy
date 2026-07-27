"""Service helpers for generic host-managed LLM workflows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.api.llm_tasks import LlmTaskActionResponse
from app.models.contracts import (
    LlmTaskActionStatus,
    LlmTaskApprovalPolicy,
    LlmTaskKind,
    LlmTaskMode,
    LlmTaskStatus,
    LlmWorkflowState,
)
from app.models.db import LlmTask, LlmTaskAction


class LlmTaskError(ValueError):
    """Raised when an LLM workflow transition is invalid."""


@dataclass(frozen=True)
class LlmTaskPaths:
    """Workspace paths for one LLM task inside a user-scoped VM namespace."""

    vm_namespace: str
    workspace_path: str
    shared_workspace_path: str


def utcnow() -> datetime:
    """Return the repo's normalized naive UTC timestamp shape."""
    return datetime.now(UTC).replace(tzinfo=None)


def build_llm_task_paths(*, user_id: int, llm_task_id: int) -> LlmTaskPaths:
    """Return stable VM paths for one task and the user's shared workspace."""
    vm_namespace = f"user:{user_id}"
    root = _normalized_sandbox_root()
    return LlmTaskPaths(
        vm_namespace=vm_namespace,
        workspace_path=f"{root}/tasks/{llm_task_id}",
        shared_workspace_path=f"{root}/users/{user_id}/shared",
    )


def _normalized_sandbox_root() -> str:
    root = PurePosixPath(get_settings().llm_task_sandbox_root.strip() or "/tmp/newsly")
    if not root.is_absolute() or ".." in root.parts:
        raise LlmTaskError("LLM task sandbox root must be an absolute path without '..'")
    return root.as_posix().rstrip("/")


def create_llm_task(
    db: Session,
    *,
    user_id: int,
    task_kind: LlmTaskKind | str,
    mode: LlmTaskMode | str,
    workflow_key: str,
    workflow_version: int = 1,
    subject_id: int | None = None,
    parent_task_id: int | None = None,
    approval_policy: dict[str, Any] | None = None,
    allowed_actions: list[str] | None = None,
    tool_policy: dict[str, Any] | None = None,
    prompt_pack: str | None = None,
    input_json: dict[str, Any] | None = None,
    status: LlmTaskStatus | str = LlmTaskStatus.QUEUED,
    workflow_state: LlmWorkflowState | str = LlmWorkflowState.QUEUED,
) -> LlmTask:
    """Create a generic LLM task row and initialize its workspace metadata."""
    status_value = _enum_value(status)
    workflow_state_value = _enum_value(workflow_state)
    task = LlmTask(
        user_id=user_id,
        task_kind=_enum_value(task_kind),
        mode=_enum_value(mode),
        workflow_key=workflow_key,
        workflow_version=workflow_version,
        subject_id=subject_id,
        parent_task_id=parent_task_id,
        workflow_state=workflow_state_value,
        status=status_value,
        approval_policy=approval_policy or {},
        allowed_actions=allowed_actions or [],
        tool_policy=tool_policy or {},
        prompt_pack=prompt_pack,
        input_json=input_json or {},
        output_json={},
        artifact_manifest={},
        usage_json={},
        status_history=[],
    )
    append_llm_task_status_history(
        task,
        status=status_value,
        workflow_state=workflow_state_value,
        note="LLM task created",
    )
    db.add(task)
    db.flush()
    task_id = require_llm_task_id(task)
    paths = build_llm_task_paths(user_id=user_id, llm_task_id=task_id)
    task.vm_namespace = paths.vm_namespace
    task.workspace_path = paths.workspace_path
    task.shared_workspace_path = paths.shared_workspace_path
    db.flush()
    return task


def require_llm_task_id(task: LlmTask) -> int:
    """Return a persisted task id or raise."""
    if task.id is None:
        raise LlmTaskError("LLM task is missing an id")
    return int(task.id)


def get_llm_task(
    db: Session,
    *,
    user_id: int,
    llm_task_id: int,
) -> LlmTask | None:
    """Return one LLM task owned by a user."""
    return db.query(LlmTask).filter(LlmTask.id == llm_task_id, LlmTask.user_id == user_id).first()


def get_llm_task_action(
    db: Session,
    *,
    task: LlmTask,
    action_id: int,
) -> LlmTaskAction | None:
    """Return one action that belongs to a task."""
    return (
        db.query(LlmTaskAction)
        .filter(
            LlmTaskAction.id == action_id,
            LlmTaskAction.llm_task_id == require_llm_task_id(task),
        )
        .first()
    )


def list_llm_task_actions(
    db: Session,
    *,
    task: LlmTask,
) -> list[LlmTaskAction]:
    """Return all actions for a task in creation order."""
    return (
        db.query(LlmTaskAction)
        .filter(LlmTaskAction.llm_task_id == require_llm_task_id(task))
        .order_by(LlmTaskAction.created_at, LlmTaskAction.id)
        .all()
    )


def present_llm_task_action(action: LlmTaskAction) -> LlmTaskActionResponse:
    """Convert an action row into the API DTO."""
    if action.id is None or action.llm_task_id is None:
        raise LlmTaskError("LLM task action is missing persisted identifiers")
    if action.created_at is None:
        raise LlmTaskError("LLM task action is missing created_at")
    return LlmTaskActionResponse(
        id=int(action.id),
        llm_task_id=int(action.llm_task_id),
        action_name=str(action.action_name),
        action_status=LlmTaskActionStatus(str(action.action_status)),
        approval_policy=LlmTaskApprovalPolicy(str(action.approval_policy)),
        approval_required=bool(action.approval_required),
        action_input=action.action_input if isinstance(action.action_input, dict) else {},
        action_result=action.action_result if isinstance(action.action_result, dict) else {},
        rationale=action.rationale,
        idempotency_key=action.idempotency_key,
        approved_by_user_id=action.approved_by_user_id,
        error_message=action.error_message,
        created_at=action.created_at,
        approved_at=action.approved_at,
        started_at=action.started_at,
        completed_at=action.completed_at,
    )


def set_llm_task_status(
    db: Session,
    task: LlmTask,
    *,
    status: LlmTaskStatus | str,
    workflow_state: LlmWorkflowState | str | None = None,
    note: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    output_json: dict[str, Any] | None = None,
    artifact_manifest: dict[str, Any] | None = None,
    model_provider: str | None = None,
    model_name: str | None = None,
    sandbox_provider: str | None = None,
    sandbox_id: str | None = None,
    agent_log_object_key: str | None = None,
) -> None:
    """Set task status, append history, and update related execution metadata."""
    now = utcnow()
    status_value = _enum_value(status)
    workflow_state_value = _enum_value(workflow_state) if workflow_state else status_value
    task.status = status_value
    task.workflow_state = workflow_state_value
    if (
        status_value
        in {
            LlmTaskStatus.PREPARING.value,
            LlmTaskStatus.RUNNING.value,
            LlmTaskStatus.APPLYING.value,
        }
        and task.started_at is None
    ):
        task.started_at = now
    if status_value in {
        LlmTaskStatus.COMPLETED.value,
        LlmTaskStatus.FAILED.value,
        LlmTaskStatus.CANCELLED.value,
    }:
        task.completed_at = now
    if error_type is not None:
        task.error_type = error_type
    if error_message is not None:
        task.error_message = error_message
    if output_json is not None:
        task.output_json = output_json
    if artifact_manifest is not None:
        task.artifact_manifest = artifact_manifest
    if model_provider is not None:
        task.model_provider = model_provider
    if model_name is not None:
        task.model_name = model_name
    if sandbox_provider is not None:
        task.sandbox_provider = sandbox_provider
    if sandbox_id is not None:
        task.sandbox_id = sandbox_id
    if agent_log_object_key is not None:
        task.agent_log_object_key = agent_log_object_key
    task.updated_at = now
    append_llm_task_status_history(
        task,
        status=status_value,
        workflow_state=workflow_state_value,
        note=note,
    )
    db.flush()


def append_llm_task_status_history(
    task: LlmTask,
    *,
    status: LlmTaskStatus | str,
    workflow_state: LlmWorkflowState | str,
    note: str | None = None,
) -> None:
    """Append one status entry to the task's JSON status history."""
    history = list(task.status_history or [])
    entry: dict[str, Any] = {
        "status": _enum_value(status),
        "workflow_state": _enum_value(workflow_state),
        "created_at": utcnow().isoformat(),
    }
    if note:
        entry["note"] = note
    history.append(entry)
    task.status_history = history


def request_llm_task_action(
    db: Session,
    *,
    task: LlmTask,
    action_name: str,
    action_input: dict[str, Any],
    rationale: str | None = None,
    idempotency_key: str | None = None,
) -> LlmTaskAction:
    """Record an action request and apply the task's approval policy."""
    return _create_action(
        db,
        task=task,
        action_name=action_name,
        action_input=action_input,
        rationale=rationale,
        idempotency_key=idempotency_key,
        force_proposed=False,
    )


def approve_llm_task_action(
    db: Session,
    *,
    action: LlmTaskAction,
    approved_by_user_id: int,
) -> None:
    """Approve an awaiting action so the host can apply its stored input."""
    if action.action_status != LlmTaskActionStatus.AWAITING_APPROVAL.value:
        raise LlmTaskError(f"Action cannot be approved from status {action.action_status!r}")
    action.action_status = LlmTaskActionStatus.APPROVED.value
    action.approved_by_user_id = approved_by_user_id
    action.approved_at = utcnow()
    action.updated_at = utcnow()
    db.flush()


def reject_llm_task_action(
    db: Session,
    *,
    action: LlmTaskAction,
    error_message: str | None = None,
) -> None:
    """Reject an awaiting/proposed action."""
    if action.action_status not in {
        LlmTaskActionStatus.AWAITING_APPROVAL.value,
        LlmTaskActionStatus.PROPOSED.value,
    }:
        raise LlmTaskError(f"Action cannot be rejected from status {action.action_status!r}")
    action.action_status = LlmTaskActionStatus.REJECTED.value
    action.error_message = error_message
    action.completed_at = utcnow()
    action.updated_at = utcnow()
    db.flush()


def mark_llm_task_action_applied(
    db: Session,
    *,
    action: LlmTaskAction,
    action_result: dict[str, Any] | None = None,
) -> None:
    """Mark a host action as applied after the domain service succeeds."""
    action.action_status = LlmTaskActionStatus.APPLIED.value
    action.action_result = action_result or {}
    action.completed_at = utcnow()
    action.updated_at = utcnow()
    db.flush()


def mark_llm_task_action_failed(
    db: Session,
    *,
    action: LlmTaskAction,
    error_message: str,
) -> None:
    """Mark a host action as failed after validation or application fails."""
    action.action_status = LlmTaskActionStatus.FAILED.value
    action.error_message = error_message
    action.completed_at = utcnow()
    action.updated_at = utcnow()
    db.flush()


def resolve_action_approval_policy(
    task: LlmTask,
    *,
    action_name: str,
) -> LlmTaskApprovalPolicy:
    """Return the effective approval policy for one action in one task."""
    raw_task_policy = task.approval_policy
    raw_policy: dict[str, Any] = raw_task_policy if isinstance(raw_task_policy, dict) else {}
    raw_overrides = raw_policy.get("overrides")
    overrides = raw_overrides if isinstance(raw_overrides, dict) else {}
    raw_value = overrides.get(action_name, raw_policy.get("default"))
    if raw_value is None:
        raw_value = LlmTaskApprovalPolicy.APPROVAL_REQUIRED.value
    try:
        return LlmTaskApprovalPolicy(str(raw_value))
    except ValueError as exc:
        raise LlmTaskError(f"Unsupported approval policy for {action_name}: {raw_value}") from exc


def _create_action(
    db: Session,
    *,
    task: LlmTask,
    action_name: str,
    action_input: dict[str, Any],
    rationale: str | None,
    idempotency_key: str | None,
    force_proposed: bool,
) -> LlmTaskAction:
    task_id = require_llm_task_id(task)
    _validate_allowed_action(task, action_name)
    if idempotency_key:
        existing = (
            db.query(LlmTaskAction)
            .filter(
                LlmTaskAction.llm_task_id == task_id,
                LlmTaskAction.action_name == action_name,
                LlmTaskAction.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing is not None:
            return existing

    policy = resolve_action_approval_policy(task, action_name=action_name)
    if force_proposed:
        status = LlmTaskActionStatus.PROPOSED
        approval_required = True
    elif policy == LlmTaskApprovalPolicy.AUTO_APPLY:
        status = LlmTaskActionStatus.APPROVED
        approval_required = False
    elif policy == LlmTaskApprovalPolicy.DRY_RUN:
        status = LlmTaskActionStatus.PROPOSED
        approval_required = False
    else:
        status = LlmTaskActionStatus.AWAITING_APPROVAL
        approval_required = True
        task.status = LlmTaskStatus.AWAITING_APPROVAL.value
        task.workflow_state = LlmWorkflowState.AWAITING_APPROVAL.value
        append_llm_task_status_history(
            task,
            status=LlmTaskStatus.AWAITING_APPROVAL,
            workflow_state=LlmWorkflowState.AWAITING_APPROVAL,
            note=f"Awaiting approval for {action_name}",
        )

    action = LlmTaskAction(
        llm_task_id=task_id,
        action_name=action_name,
        action_status=status.value,
        approval_policy=policy.value,
        approval_required=approval_required,
        action_input=_jsonable_dict(action_input),
        action_result={},
        rationale=rationale,
        idempotency_key=idempotency_key,
    )
    db.add(action)
    db.flush()
    return action


def _validate_allowed_action(task: LlmTask, action_name: str) -> None:
    allowed = task.allowed_actions if isinstance(task.allowed_actions, list) else []
    if allowed and action_name not in {str(item) for item in allowed}:
        raise LlmTaskError(f"Action is not allowed for this workflow: {action_name}")


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _jsonable_dict(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, default=str))
