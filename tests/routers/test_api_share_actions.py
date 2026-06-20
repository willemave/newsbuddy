from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.api.share_actions import ShareActionCreateRequest
from app.models.contracts import LlmTaskActionStatus, LlmTaskApprovalPolicy, LlmTaskMode
from app.models.db import Content, LlmTask
from app.services.llm_tasks import request_llm_task_action
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
