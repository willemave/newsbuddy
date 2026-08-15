from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.api.share_actions import ShareActionCreateRequest
from app.models.contracts import (
    LlmTaskActionStatus,
    LlmTaskApprovalPolicy,
    LlmTaskMode,
    LlmTaskStatus,
    LlmWorkflowState,
)
from app.models.db import Content, LlmTask, LlmTaskAction
from app.services import share_actions
from app.services.llm_tasks import request_llm_task_action, set_llm_task_status
from app.services.share_actions import create_share_action


def _share_request(**values: object) -> ShareActionCreateRequest:
    return ShareActionCreateRequest.model_validate(values)


def test_create_share_action_endpoint(client: TestClient, db_session: Session, test_user) -> None:
    response = client.post(
        "/api/share-actions",
        json={"url": "https://example.com/feed-page", "mode": "add_feed"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["mode"] == "add_feed"
    task = db_session.query(LlmTask).filter_by(id=payload["task_id"]).one()
    assert task.user_id == test_user.id
    assert task.task_kind == "share_action"


def test_create_share_action_endpoint_accepts_add_to_briefing(
    client: TestClient,
    db_session: Session,
    test_user,
) -> None:
    response = client.post(
        "/api/share-actions",
        json={"url": "https://example.com/story", "mode": "add_to_briefing"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["mode"] == "add_to_briefing"
    task = db_session.query(LlmTask).filter_by(id=payload["task_id"]).one()
    assert task.allowed_actions == ["add_to_briefing"]
    assert task.prompt_pack == "share_action.add_to_briefing"


def test_create_share_action_endpoint_persists_deck_instructions(
    client: TestClient,
    db_session: Session,
) -> None:
    response = client.post(
        "/api/share-actions",
        json={
            "url": "https://example.com/research",
            "mode": "presentation",
            "interests_prompt": "Compare the studies and investigate conflicting results.",
        },
    )

    assert response.status_code == 202
    task = db_session.query(LlmTask).filter_by(id=response.json()["task_id"]).one()
    input_json = cast(dict[str, Any], task.input_json)
    assert input_json["interests_prompt"] == (
        "Compare the studies and investigate conflicting results."
    )


def test_get_share_action_endpoint(client: TestClient, db_session: Session, test_user) -> None:
    created = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/story",
            mode=LlmTaskMode.ADD_CONTENT,
        ),
    )

    response = client.get(f"/api/share-actions/{created.task_id}")

    assert response.status_code == 200
    assert response.json()["task_id"] == created.task_id


def test_approve_share_action_applies_stored_action(
    client: TestClient,
    db_session: Session,
    test_user,
) -> None:
    created = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/feed-page",
            mode=LlmTaskMode.ADD_FEED,
            approval_policy={"default": LlmTaskApprovalPolicy.APPROVAL_REQUIRED.value},
        ),
    )
    task = db_session.query(LlmTask).filter_by(id=created.task_id).one()
    action = request_llm_task_action(
        db_session,
        task=task,
        action_name="subscribe_to_feed",
        action_input={"url": "https://example.com/feed.xml"},
    )
    db_session.commit()

    response = client.post(f"/api/llm-tasks/{task.id}/actions/{action.id}/approve")

    assert response.status_code == 200
    payload = response.json()
    assert payload["action_status"] == LlmTaskActionStatus.APPLIED.value
    content = db_session.query(Content).filter_by(url="https://example.com/feed.xml").one()
    assert content.id is not None


def test_approve_share_action_failure_terminally_fails_parent_task(
    client: TestClient,
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    created = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/feed-page",
            mode=LlmTaskMode.ADD_FEED,
            approval_policy={"default": LlmTaskApprovalPolicy.APPROVAL_REQUIRED.value},
        ),
    )
    task = db_session.query(LlmTask).filter_by(id=created.task_id).one()
    action = request_llm_task_action(
        db_session,
        task=task,
        action_name="subscribe_to_feed",
        action_input={"url": "https://example.com/feed.xml"},
    )
    set_llm_task_status(
        db_session,
        task,
        status=LlmTaskStatus.AWAITING_APPROVAL,
        workflow_state=LlmWorkflowState.AWAITING_APPROVAL,
        note="Awaiting test approval",
    )
    db_session.commit()

    def fail_apply(*_args, **_kwargs):
        raise RuntimeError("feed application unavailable")

    monkeypatch.setattr(share_actions, "_apply_action", fail_apply)

    response = client.post(f"/api/llm-tasks/{task.id}/actions/{action.id}/approve")

    assert response.status_code == 400
    assert response.json()["detail"] == "feed application unavailable"
    db_session.expire_all()
    persisted_task = db_session.query(LlmTask).filter_by(id=task.id).one()
    persisted_action = db_session.query(LlmTaskAction).filter_by(id=action.id).one()
    assert persisted_action.action_status == LlmTaskActionStatus.FAILED.value
    assert persisted_action.error_message == "feed application unavailable"
    assert persisted_task.status == LlmTaskStatus.FAILED.value
    assert persisted_task.workflow_state == LlmWorkflowState.FAILED.value
    assert persisted_task.error_type == "RuntimeError"
    assert persisted_task.error_message == "feed application unavailable"
