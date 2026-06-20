from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.core.db import Base
from app.models.contracts import (
    LlmTaskActionStatus,
    LlmTaskApprovalPolicy,
    LlmTaskStatus,
    LlmWorkflowState,
)
from app.models.db.common import _utcnow


class LlmTask(Base):
    """Generic ledger row for one host-managed LLM workflow run."""

    __tablename__ = "llm_tasks"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_kind = Column(String(64), nullable=False, index=True)
    mode = Column(String(64), nullable=False, index=True)
    workflow_key = Column(String(128), nullable=False, index=True)
    workflow_version = Column(Integer, nullable=False, default=1)
    workflow_state = Column(
        String(32),
        nullable=False,
        default=LlmWorkflowState.QUEUED.value,
        index=True,
    )
    status = Column(String(32), nullable=False, default=LlmTaskStatus.QUEUED.value, index=True)

    approval_policy = Column(JSONB, nullable=False, default=dict)
    allowed_actions = Column(JSONB, nullable=False, default=list)
    tool_policy = Column(JSONB, nullable=False, default=dict)

    vm_namespace = Column(String(255), nullable=True, index=True)
    sandbox_provider = Column(String(50), nullable=True)
    sandbox_id = Column(String(255), nullable=True, index=True)
    workspace_path = Column(String(2048), nullable=True)
    shared_workspace_path = Column(String(2048), nullable=True)
    prompt_pack = Column(String(255), nullable=True)

    input_json = Column(JSONB, nullable=False, default=dict)
    output_json = Column(JSONB, nullable=False, default=dict)
    artifact_manifest = Column(JSONB, nullable=False, default=dict)
    usage_json = Column(JSONB, nullable=False, default=dict)
    status_history = Column(JSONB, nullable=False, default=list)

    model_provider = Column(String(50), nullable=True)
    model_name = Column(String(100), nullable=True)
    agent_log_object_key = Column(String(2048), nullable=True)
    error_type = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_llm_tasks_user_status_created", "user_id", "status", "created_at"),
        Index("idx_llm_tasks_kind_mode_created", "task_kind", "mode", "created_at"),
        Index("idx_llm_tasks_workflow_state", "workflow_key", "workflow_state"),
    )


class LlmTaskAction(Base):
    """Host-mediated action requested during one LLM task workflow."""

    __tablename__ = "llm_task_actions"

    id = Column(Integer, primary_key=True)
    llm_task_id = Column(
        Integer,
        ForeignKey("llm_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_name = Column(String(128), nullable=False, index=True)
    action_status = Column(
        String(32),
        nullable=False,
        default=LlmTaskActionStatus.PROPOSED.value,
        index=True,
    )
    approval_policy = Column(
        String(32),
        nullable=False,
        default=LlmTaskApprovalPolicy.APPROVAL_REQUIRED.value,
    )
    approval_required = Column(Boolean, nullable=False, default=True)
    action_input = Column(JSONB, nullable=False, default=dict)
    action_result = Column(JSONB, nullable=False, default=dict)
    rationale = Column(Text, nullable=True)
    idempotency_key = Column(String(512), nullable=True, index=True)
    approved_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
    approved_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_llm_task_actions_task_status", "llm_task_id", "action_status", "created_at"),
        Index(
            "uq_llm_task_actions_idempotency",
            "llm_task_id",
            "action_name",
            "idempotency_key",
            unique=True,
        ),
    )
