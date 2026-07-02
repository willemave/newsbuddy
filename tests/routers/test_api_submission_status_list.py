"""Tests for user submission status list endpoint."""

from datetime import datetime

from app.models.contracts import (
    ContentStatus,
    ContentType,
    LearningDeckRunStatus,
    LearningDeckSourceKind,
    LlmTaskActionStatus,
    LlmTaskApprovalPolicy,
    LlmTaskKind,
    LlmTaskMode,
    LlmTaskStatus,
)
from app.models.db import LearningDeck, LearningDeckRun, LlmTask, LlmTaskAction


def test_submission_status_list_projects_share_action_tasks(
    client,
    db_session,
    content_factory,
    test_user,
) -> None:
    now = datetime(2026, 6, 28, 12, 0, 0)
    processing = content_factory(
        url="https://example.com/processing",
        source_url="https://example.com/processing",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.PROCESSING.value,
        title="Processing Item",
        content_metadata={
            "submitted_by_user_id": test_user.id,
            "submitted_via": "share_action",
        },
    )
    skipped = content_factory(
        url="https://example.com/skipped",
        source_url="https://example.com/skipped",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.SKIPPED.value,
        title="Skipped Item",
        content_metadata={
            "submitted_by_user_id": test_user.id,
            "submitted_via": "share_action",
        },
    )
    feed_subscription = content_factory(
        url="https://example.com/feed-request",
        source_url="https://example.com/feed-request",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.SKIPPED.value,
        title="Feed Request",
        content_metadata={
            "submitted_by_user_id": test_user.id,
            "submitted_via": "share_action",
            "subscribe_to_feed": True,
            "detected_feed": {
                "url": "https://example.com/feed.xml",
                "type": "atom",
                "title": "Example Feed",
                "format": "rss",
            },
            "feed_subscription": {
                "status": "created",
                "feed_url": "https://example.com/feed.xml",
                "feed_type": "atom",
                "created": True,
                "config_id": 12,
                "initial_download": {
                    "requested_count": 2,
                    "ran": True,
                    "status": "completed",
                    "saved": 3,
                    "duplicates": 0,
                    "errors": 0,
                },
            },
        },
    )
    direct_content = content_factory(
        url="https://example.com/direct",
        source_url="https://example.com/direct",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.PROCESSING.value,
        title="Direct Submit",
        content_metadata={
            "submitted_by_user_id": test_user.id,
            "submitted_via": "share_sheet",
        },
    )

    processing_task = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_CONTENT,
        url="https://example.com/processing",
        created_at=now,
        action_name="add_content",
        action_result={"content_id": processing.id},
    )
    skipped_task = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_CONTENT,
        url="https://example.com/skipped",
        created_at=now,
        action_name="add_content",
        action_result={"content_id": skipped.id},
    )
    feed_task = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_FEED,
        url="https://example.com/feed-request",
        created_at=now,
        action_name="subscribe_to_feed",
        action_result={"content_id": feed_subscription.id},
    )
    deck, deck_run = _create_learning_deck_target(
        db_session,
        user_id=test_user.id,
        created_at=now,
    )
    deck_task = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.PRESENTATION,
        url="https://github.com/deepseek-ai/DeepSpec/blob/main/DSpark_paper.pdf",
        created_at=now,
        action_name="create_learning_deck",
        action_input={"source_url": deck.source_url},
        action_result={"learning_deck_id": deck.id},
    )
    failed_task = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_CONTENT,
        url="https://example.com/failed",
        created_at=now,
        status=LlmTaskStatus.FAILED,
        error_message="Share Action failed",
    )

    response = client.get("/api/content/submissions/list")
    assert response.status_code == 200
    payload = response.json()

    ids = {item["id"] for item in payload["submissions"]}
    assert processing_task.id in ids
    assert skipped_task.id in ids
    assert feed_task.id in ids
    assert deck_task.id in ids
    assert failed_task.id in ids
    assert deck_run.id is not None
    assert -deck_run.id not in ids
    assert all(item["url"] != direct_content.url for item in payload["submissions"])

    failed_item = next(item for item in payload["submissions"] if item["id"] == failed_task.id)
    assert failed_item["error_message"] == "Share Action failed"
    assert failed_item["outcome"] == "failed"

    skipped_item = next(item for item in payload["submissions"] if item["id"] == skipped_task.id)
    assert skipped_item["submission_kind"] == "content"
    assert skipped_item["outcome"] == "skipped"

    feed_item = next(item for item in payload["submissions"] if item["id"] == feed_task.id)
    assert feed_item["submission_kind"] == "feed_subscription"
    assert feed_item["outcome"] == "subscribed"
    assert feed_item["detected_feed"]["url"] == "https://example.com/feed.xml"
    assert feed_item["feed_subscription"]["initial_download"]["saved"] == 3

    deck_item = next(item for item in payload["submissions"] if item["id"] == deck_task.id)
    assert deck_item["submission_kind"] == "learning_deck"
    assert deck_item["title"] == "deepseek-ai/DeepSpec: DSpark_paper.pdf"
    assert deck_item["outcome"] == "completed"


def _create_share_action_task(
    db_session,
    *,
    user_id: int,
    mode: LlmTaskMode,
    url: str,
    created_at: datetime,
    status: LlmTaskStatus = LlmTaskStatus.COMPLETED,
    action_name: str | None = None,
    action_status: LlmTaskActionStatus = LlmTaskActionStatus.APPLIED,
    action_input: dict | None = None,
    action_result: dict | None = None,
    error_message: str | None = None,
) -> LlmTask:
    completed_at = created_at if status in {LlmTaskStatus.COMPLETED, LlmTaskStatus.FAILED} else None
    task = LlmTask(
        user_id=user_id,
        task_kind=LlmTaskKind.SHARE_ACTION.value,
        mode=mode.value,
        workflow_key=f"share_action.{mode.value}.v1",
        workflow_state=status.value,
        status=status.value,
        approval_policy={"default": LlmTaskApprovalPolicy.AUTO_APPLY.value},
        allowed_actions=[action_name] if action_name else [],
        tool_policy={},
        input_json={"url": url, "mode": mode.value},
        output_json={},
        artifact_manifest={},
        usage_json={},
        status_history=[],
        error_message=error_message,
        created_at=created_at,
        updated_at=created_at,
        completed_at=completed_at,
    )
    db_session.add(task)
    db_session.flush()
    if action_name is not None:
        db_session.add(
            LlmTaskAction(
                llm_task_id=task.id,
                action_name=action_name,
                action_status=action_status.value,
                approval_policy=LlmTaskApprovalPolicy.AUTO_APPLY.value,
                approval_required=False,
                action_input=action_input or {},
                action_result=action_result or {},
                created_at=created_at,
                updated_at=created_at,
                completed_at=created_at if action_status == LlmTaskActionStatus.APPLIED else None,
            )
        )
    db_session.commit()
    db_session.refresh(task)
    return task


def _create_learning_deck_target(
    db_session,
    *,
    user_id: int,
    created_at: datetime,
) -> tuple[LearningDeck, LearningDeckRun]:
    deck = LearningDeck(
        user_id=user_id,
        source_kind=LearningDeckSourceKind.GITHUB_REPO.value,
        source_identity="github:deepseek-ai/deepspec:file:main/DSpark_paper.pdf",
        source_url="https://github.com/deepseek-ai/DeepSpec/blob/main/DSpark_paper.pdf",
        source_content_id=None,
        source_title="deepseek-ai/DeepSpec: DSpark_paper.pdf",
        source_metadata={"submission": {"submitted_via": "share_action"}},
        title="deepseek-ai/DeepSpec: DSpark_paper.pdf",
        artifact_object_keys=[],
        share_enabled=False,
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(deck)
    db_session.flush()
    run = LearningDeckRun(
        deck_id=deck.id,
        user_id=user_id,
        status=LearningDeckRunStatus.COMPLETED.value,
        source_snapshot={
            "source_kind": LearningDeckSourceKind.GITHUB_REPO.value,
            "source_identity": deck.source_identity,
            "source_url": deck.source_url,
            "source_title": deck.source_title,
            "source_metadata": deck.source_metadata,
        },
        timeline=[],
        artifact_object_keys=[],
        created_at=created_at,
        updated_at=created_at,
        completed_at=created_at,
    )
    db_session.add(run)
    db_session.flush()
    deck.latest_run_id = run.id
    deck.latest_successful_run_id = run.id
    db_session.commit()
    db_session.refresh(deck)
    db_session.refresh(run)
    return deck, run
