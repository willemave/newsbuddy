"""Small helper for durable LLM task rows around chat-style turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models.contracts import (
    LlmTaskKind,
    LlmTaskMode,
    LlmTaskStatus,
    LlmWorkflowState,
)
from app.models.db import LlmTask
from app.services.llm_tasks import LlmTaskError, create_llm_task, set_llm_task_status


@dataclass(frozen=True)
class LlmTaskTurnSpec:
    """Static LLM task metadata shared by a family of chat-style turns."""

    task_kind: LlmTaskKind
    mode: LlmTaskMode
    workflow_key: str
    approval_policy: dict[str, str]
    allowed_actions: list[str] = field(default_factory=list)
    tool_policy: dict[str, Any] = field(default_factory=dict)
    prompt_pack: str | None = None


@dataclass(frozen=True)
class LlmTaskTurnTracker:
    """Handle best-effort status updates for one durable LLM task row."""

    task_id: int | None

    @classmethod
    def create(
        cls,
        db: Session,
        *,
        user_id: int,
        spec: LlmTaskTurnSpec,
        input_json: dict[str, Any],
    ) -> LlmTaskTurnTracker:
        llm_task = create_llm_task(
            db,
            user_id=user_id,
            task_kind=spec.task_kind,
            mode=spec.mode,
            workflow_key=spec.workflow_key,
            approval_policy=spec.approval_policy,
            allowed_actions=spec.allowed_actions,
            tool_policy=spec.tool_policy,
            prompt_pack=spec.prompt_pack,
            input_json=input_json,
            status=LlmTaskStatus.PREPARING,
            workflow_state=LlmWorkflowState.PREPARING,
        )
        if llm_task.id is None:
            raise LlmTaskError("LLM task turn row was not created")
        db.commit()
        return cls(task_id=int(llm_task.id))

    def running(
        self,
        db: Session,
        *,
        note: str,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> None:
        self.set_status(
            db,
            status=LlmTaskStatus.RUNNING,
            workflow_state=LlmWorkflowState.RUNNING,
            note=note,
            model_provider=model_provider,
            model_name=model_name,
        )

    def completed(
        self,
        db: Session,
        *,
        note: str,
        output_json: dict[str, object] | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> None:
        self.set_status(
            db,
            status=LlmTaskStatus.COMPLETED,
            workflow_state=LlmWorkflowState.COMPLETED,
            note=note,
            output_json=output_json,
            model_provider=model_provider,
            model_name=model_name,
        )

    def failed(
        self,
        db: Session,
        *,
        note: str,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.set_status(
            db,
            status=LlmTaskStatus.FAILED,
            workflow_state=LlmWorkflowState.FAILED,
            note=note,
            error_type=error_type,
            error_message=error_message,
        )

    def cancelled(
        self,
        db: Session,
        *,
        note: str,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Record an interrupted attempt without classifying it as a chat failure."""
        self.set_status(
            db,
            status=LlmTaskStatus.CANCELLED,
            workflow_state=LlmWorkflowState.CANCELLED,
            note=note,
            error_type=error_type,
            error_message=error_message,
        )

    def set_status(
        self,
        db: Session,
        *,
        status: LlmTaskStatus,
        workflow_state: LlmWorkflowState,
        note: str,
        output_json: dict[str, object] | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if self.task_id is None:
            return
        task = db.query(LlmTask).filter(LlmTask.id == self.task_id).first()
        if task is None:
            return
        set_llm_task_status(
            db,
            task,
            status=status,
            workflow_state=workflow_state,
            note=note,
            output_json=output_json,
            model_provider=model_provider,
            model_name=model_name,
            error_type=error_type,
            error_message=error_message,
        )
        db.commit()
