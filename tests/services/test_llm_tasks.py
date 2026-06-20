from __future__ import annotations

import pytest

from app.core.model_defaults import OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC
from app.core.settings import get_settings
from app.models.contracts import (
    LlmTaskActionStatus,
    LlmTaskApprovalPolicy,
    LlmTaskKind,
    LlmTaskMode,
    LlmTaskStatus,
    LlmWorkflowState,
)
from app.models.db import LlmTaskAction
from app.services.llm_tasks import (
    LlmTaskError,
    approve_llm_task_action,
    build_llm_task_paths,
    create_llm_task,
    mark_llm_task_action_applied,
    reject_llm_task_action,
    request_llm_task_action,
)


def test_vm_task_models_default_to_deepseek_flash_v4() -> None:
    settings = get_settings()

    assert settings.llm_task_model == OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC
    assert settings.learning_deck_model == OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC


def test_build_llm_task_paths_uses_configured_sandbox_root(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_task_sandbox_root", "/tmp/newsly-agent")

    paths = build_llm_task_paths(user_id=7, llm_task_id=42)

    assert paths.vm_namespace == "user:7"
    assert paths.workspace_path == "/tmp/newsly-agent/tasks/42"
    assert paths.shared_workspace_path == "/tmp/newsly-agent/users/7/shared"


def test_build_llm_task_paths_rejects_unsafe_sandbox_root(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_task_sandbox_root", "relative/newsly")

    with pytest.raises(LlmTaskError, match="sandbox root"):
        build_llm_task_paths(user_id=7, llm_task_id=42)


def test_request_action_with_approval_required_pauses_task(db_session, test_user) -> None:
    task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=LlmTaskMode.ADD_FEED,
        workflow_key="share_action.add_feed.v1",
        approval_policy={"default": LlmTaskApprovalPolicy.APPROVAL_REQUIRED.value},
        allowed_actions=["subscribe_to_feed"],
    )

    action = request_llm_task_action(
        db_session,
        task=task,
        action_name="subscribe_to_feed",
        action_input={"feed_url": "https://example.com/feed.xml"},
        rationale="Best discovered feed",
        idempotency_key="feed:https://example.com/feed.xml",
    )

    assert task.status == LlmTaskStatus.AWAITING_APPROVAL.value
    assert task.workflow_state == LlmWorkflowState.AWAITING_APPROVAL.value
    assert action.action_status == LlmTaskActionStatus.AWAITING_APPROVAL.value
    assert action.approval_required is True
    assert action.approval_policy == LlmTaskApprovalPolicy.APPROVAL_REQUIRED.value
    assert action.rationale == "Best discovered feed"


def test_request_action_with_auto_apply_starts_approved(db_session, test_user) -> None:
    task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=LlmTaskMode.BOOKMARK_ONLY,
        workflow_key="share_action.bookmark_only.v1",
        approval_policy={"default": LlmTaskApprovalPolicy.AUTO_APPLY.value},
        allowed_actions=["save_to_knowledge"],
    )

    action = request_llm_task_action(
        db_session,
        task=task,
        action_name="save_to_knowledge",
        action_input={"content_url": "https://example.com/story"},
    )

    assert task.status == LlmTaskStatus.QUEUED.value
    assert action.action_status == LlmTaskActionStatus.APPROVED.value
    assert action.approval_required is False
    assert action.approval_policy == LlmTaskApprovalPolicy.AUTO_APPLY.value


def test_action_approval_and_apply_transitions(db_session, test_user) -> None:
    task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=LlmTaskMode.ADD_FEED,
        workflow_key="share_action.add_feed.v1",
        approval_policy={"default": LlmTaskApprovalPolicy.APPROVAL_REQUIRED.value},
        allowed_actions=["subscribe_to_feed"],
    )
    action = request_llm_task_action(
        db_session,
        task=task,
        action_name="subscribe_to_feed",
        action_input={"feed_url": "https://example.com/feed.xml"},
    )

    approve_llm_task_action(db_session, action=action, approved_by_user_id=test_user.id)
    mark_llm_task_action_applied(
        db_session,
        action=action,
        action_result={"feed_config_id": 12},
    )

    assert action.action_status == LlmTaskActionStatus.APPLIED.value
    assert action.approved_by_user_id == test_user.id
    assert action.action_result == {"feed_config_id": 12}


def test_reject_action_only_allows_proposed_or_awaiting(db_session, test_user) -> None:
    task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=LlmTaskMode.ADD_FEED,
        workflow_key="share_action.add_feed.v1",
        approval_policy={"default": LlmTaskApprovalPolicy.APPROVAL_REQUIRED.value},
        allowed_actions=["subscribe_to_feed"],
    )
    action = request_llm_task_action(
        db_session,
        task=task,
        action_name="subscribe_to_feed",
        action_input={"feed_url": "https://example.com/feed.xml"},
    )

    reject_llm_task_action(db_session, action=action, error_message="Not this one")

    assert action.action_status == LlmTaskActionStatus.REJECTED.value
    assert action.error_message == "Not this one"
    with pytest.raises(LlmTaskError):
        reject_llm_task_action(db_session, action=action)


def test_idempotent_action_request_returns_existing_action(db_session, test_user) -> None:
    task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=LlmTaskMode.ADD_FEED,
        workflow_key="share_action.add_feed.v1",
        approval_policy={"default": LlmTaskApprovalPolicy.APPROVAL_REQUIRED.value},
        allowed_actions=["subscribe_to_feed"],
    )

    first = request_llm_task_action(
        db_session,
        task=task,
        action_name="subscribe_to_feed",
        action_input={"feed_url": "https://example.com/feed.xml"},
        idempotency_key="same-feed",
    )
    second = request_llm_task_action(
        db_session,
        task=task,
        action_name="subscribe_to_feed",
        action_input={"feed_url": "https://example.com/feed.xml"},
        idempotency_key="same-feed",
    )

    assert second.id == first.id
    assert db_session.query(LlmTaskAction).count() == 1


def test_disallowed_action_is_rejected(db_session, test_user) -> None:
    task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=LlmTaskMode.ADD_FEED,
        workflow_key="share_action.add_feed.v1",
        allowed_actions=["subscribe_to_feed"],
    )

    with pytest.raises(LlmTaskError, match="not allowed"):
        request_llm_task_action(
            db_session,
            task=task,
            action_name="save_to_knowledge",
            action_input={"content_url": "https://example.com/story"},
        )
