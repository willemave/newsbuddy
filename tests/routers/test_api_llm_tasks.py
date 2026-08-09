from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.contracts import (
    LlmTaskActionStatus,
    LlmTaskApprovalPolicy,
    LlmTaskKind,
    LlmTaskMode,
)
from app.services.llm_tasks import create_llm_task, request_llm_task_action


def _create_approval_action(db_session: Session, user_id: int):
    task = create_llm_task(
        db_session,
        user_id=user_id,
        task_kind=LlmTaskKind.GENERIC,
        mode=LlmTaskMode.GENERIC,
        workflow_key="generic.review.v1",
        approval_policy={"default": LlmTaskApprovalPolicy.APPROVAL_REQUIRED.value},
        allowed_actions=["review_action"],
    )
    action = request_llm_task_action(
        db_session,
        task=task,
        action_name="review_action",
        action_input={"target": "https://example.com/item"},
        rationale="Needs user approval",
    )
    db_session.commit()
    return task, action


def test_list_llm_task_actions(
    client: TestClient,
    db_session: Session,
    test_user,
) -> None:
    task, action = _create_approval_action(db_session, test_user.id)

    response = client.get(f"/api/llm-tasks/{task.id}/actions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["actions"][0]["id"] == action.id
    assert payload["actions"][0]["action_name"] == "review_action"
    assert payload["actions"][0]["action_status"] == LlmTaskActionStatus.AWAITING_APPROVAL.value
    assert payload["actions"][0]["action_input"] == {"target": "https://example.com/item"}


def test_approve_llm_task_action(
    client: TestClient,
    db_session: Session,
    test_user,
) -> None:
    task, action = _create_approval_action(db_session, test_user.id)

    response = client.post(f"/api/llm-tasks/{task.id}/actions/{action.id}/approve")

    assert response.status_code == 200
    payload = response.json()
    assert payload["action_status"] == LlmTaskActionStatus.APPROVED.value
    assert payload["approved_by_user_id"] == test_user.id


def test_reject_llm_task_action(
    client: TestClient,
    db_session: Session,
    test_user,
) -> None:
    task, action = _create_approval_action(db_session, test_user.id)

    response = client.post(
        f"/api/llm-tasks/{task.id}/actions/{action.id}/reject",
        json={"reason": "Wrong feed"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action_status"] == LlmTaskActionStatus.REJECTED.value
    assert payload["error_message"] == "Wrong feed"


def test_approve_rejects_invalid_transition(
    client: TestClient,
    db_session: Session,
    test_user,
) -> None:
    task, action = _create_approval_action(db_session, test_user.id)
    client.post(f"/api/llm-tasks/{task.id}/actions/{action.id}/approve")

    response = client.post(f"/api/llm-tasks/{task.id}/actions/{action.id}/approve")

    assert response.status_code == 409


def test_other_user_cannot_list_llm_task_actions(
    db_session: Session,
    test_user,
    user_factory,
    client_factory,
) -> None:
    task, _action = _create_approval_action(db_session, test_user.id)
    other_user = user_factory()

    with client_factory(user=other_user) as other_client:
        response = other_client.get(f"/api/llm-tasks/{task.id}/actions")

    assert response.status_code == 404
