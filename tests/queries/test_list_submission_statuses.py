"""Tests for submission status query orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

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
    TaskStatus,
    TaskType,
)
from app.models.db import LearningDeck, LearningDeckRun, LlmTask, LlmTaskAction
from app.queries import list_submission_statuses


def test_list_submission_statuses_projects_share_action_content_targets(
    db_session: Session,
    content_factory,
    test_user,
    user_factory,
) -> None:
    other_user = user_factory(
        apple_id="other_query_user",
        email="other-query@example.com",
        full_name="Other Query User",
    )
    now = datetime(2026, 6, 28, 12, 0, 0)
    processing_content = content_factory(
        url="https://example.com/query-processing",
        source_url="https://example.com/query-processing",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.PROCESSING.value,
        title="Processing Item",
        content_metadata={
            "processing": {
                "submitted_by_user_id": test_user.id,
                "submitted_via": "share_action",
            }
        },
    )
    completed_content = content_factory(
        url="https://example.com/query-completed",
        source_url="https://example.com/query-completed",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Completed Item",
        content_metadata={
            "processing": {
                "submitted_by_user_id": test_user.id,
                "submitted_via": "share_action",
            }
        },
    )
    awaiting_image_content = content_factory(
        url="https://example.com/query-awaiting-image",
        source_url="https://example.com/query-awaiting-image",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.AWAITING_IMAGE.value,
        title="Awaiting Image Item",
        content_metadata={
            "processing": {
                "submitted_by_user_id": test_user.id,
                "submitted_via": "share_action",
            }
        },
    )
    direct_content = content_factory(
        url="https://example.com/query-direct",
        source_url="https://example.com/query-direct",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.PROCESSING.value,
        title="Direct Content Submit",
        content_metadata={
            "processing": {
                "submitted_by_user_id": test_user.id,
                "submitted_via": "share_sheet",
            }
        },
    )

    processing_task, _ = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_CONTENT,
        url="https://example.com/query-processing",
        created_at=now,
        action_name="add_content",
        action_result={"content_id": processing_content.id},
    )
    completed_task, _ = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_CONTENT,
        url="https://example.com/query-completed",
        created_at=now - timedelta(minutes=1),
        action_name="add_content",
        action_result={"content_id": completed_content.id},
    )
    awaiting_image_task, _ = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_CONTENT,
        url="https://example.com/query-awaiting-image",
        created_at=now - timedelta(minutes=2),
        action_name="add_content",
        action_result={"content_id": awaiting_image_content.id},
    )
    other_user_task, _ = _create_share_action_task(
        db_session,
        user_id=other_user.id,
        mode=LlmTaskMode.ADD_CONTENT,
        url="https://example.com/query-other",
        created_at=now,
        action_name="add_content",
        action_result={"content_id": processing_content.id},
    )

    response = list_submission_statuses.execute(
        db_session,
        user_id=test_user.id,
        cursor=None,
        limit=10,
    )

    ids = {item.id for item in response.submissions}
    assert processing_task.id in ids
    assert completed_task.id in ids
    assert awaiting_image_task.id in ids
    assert direct_content.id not in ids
    assert other_user_task.id not in ids

    processing_item = next(item for item in response.submissions if item.id == processing_task.id)
    assert processing_item.title == "Processing Item"
    assert processing_item.status == "processing"
    assert processing_item.submitted_via == "share_action"
    assert processing_item.is_self_submission is True

    completed_item = next(item for item in response.submissions if item.id == completed_task.id)
    assert completed_item.outcome == "completed"

    awaiting_item = next(item for item in response.submissions if item.id == awaiting_image_task.id)
    assert awaiting_item.outcome == "processing"


def test_list_submission_statuses_projects_share_action_status_without_target(
    db_session: Session,
    test_user,
) -> None:
    now = datetime(2026, 6, 28, 12, 0, 0)
    running_task, _ = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_CONTENT,
        url="https://example.com/running-share",
        created_at=now,
        status=LlmTaskStatus.RUNNING,
        output_json={"title": "Running Share"},
    )
    failed_task, _ = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_CONTENT,
        url="https://example.com/failed-share",
        created_at=now - timedelta(minutes=1),
        status=LlmTaskStatus.FAILED,
        error_message="Share Action failed",
    )

    response = list_submission_statuses.execute(
        db_session,
        user_id=test_user.id,
        cursor=None,
        limit=10,
    )

    running_item = next(item for item in response.submissions if item.id == running_task.id)
    assert running_item.url == "https://example.com/running-share"
    assert running_item.title == "Running Share"
    assert running_item.status == "processing"
    assert running_item.outcome == "processing"

    failed_item = next(item for item in response.submissions if item.id == failed_task.id)
    assert failed_item.status == "failed"
    assert failed_item.outcome == "failed"
    assert failed_item.error_message == "Share Action failed"


def test_list_submission_statuses_projects_no_action_rationale_for_recovery(
    db_session: Session,
    test_user,
) -> None:
    task, _ = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_TO_BRIEFING,
        url="https://example.com/unsupported-homepage",
        created_at=datetime(2026, 6, 28, 12, 0, 0),
        output_json={
            "action": "no_action",
            "rationale": "Neither a continuing source nor an eligible item was found.",
        },
    )

    response = list_submission_statuses.execute(
        db_session,
        user_id=test_user.id,
        cursor=None,
        limit=10,
    )

    item = next(item for item in response.submissions if item.id == task.id)
    assert item.status == "completed"
    assert item.outcome == "no_action"
    assert item.rationale == "Neither a continuing source nor an eligible item was found."
    assert item.url == "https://example.com/unsupported-homepage"


def test_list_submission_statuses_includes_learning_deck_targets_only_from_share_actions(
    db_session: Session,
    test_user,
) -> None:
    now = datetime(2026, 6, 28, 12, 0, 0)
    marked_deck, marked_run = _create_learning_deck_submission(
        db_session,
        user_id=test_user.id,
        source_identity="github:deepseek-ai/DeepSpec",
        source_url="https://github.com/deepseek-ai/DeepSpec",
        title="DeepSpec",
        status=LearningDeckRunStatus.COMPLETED,
        created_at=now,
        source_metadata={"submission": {"submitted_via": "share_action"}},
    )
    manual_deck, _manual_run = _create_learning_deck_submission(
        db_session,
        user_id=test_user.id,
        source_identity="github:example/manual",
        source_url="https://github.com/example/manual",
        title="Manual Deck",
        status=LearningDeckRunStatus.FAILED,
        created_at=now - timedelta(minutes=1),
        source_metadata={},
    )
    deck_task, _ = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.PRESENTATION,
        url="https://github.com/deepseek-ai/DeepSpec",
        created_at=now,
        action_name="create_learning_deck",
        action_input={"source_url": "https://github.com/deepseek-ai/DeepSpec"},
        action_result={"learning_deck_id": marked_deck.id},
    )

    response = list_submission_statuses.execute(
        db_session,
        user_id=test_user.id,
        cursor=None,
        limit=10,
    )

    ids = {item.id for item in response.submissions}
    assert deck_task.id in ids
    assert marked_run.id is not None
    assert manual_deck.latest_run_id is not None
    assert -marked_run.id not in ids
    assert -manual_deck.latest_run_id not in ids
    deck_item = next(item for item in response.submissions if item.id == deck_task.id)
    assert deck_item.submission_kind == "learning_deck"
    assert deck_item.status == "completed"
    assert deck_item.outcome == "completed"
    assert deck_item.title == "DeepSpec"


@pytest.mark.parametrize(
    ("subscription_status", "expected_outcome"),
    [
        ("created", "subscribed"),
        ("reactivated", "subscribed"),
        ("already_exists", "already_subscribed"),
        ("no_feed_found", "feed_not_found"),
        ("fetch_failed", "feed_fetch_failed"),
        ("unsupported_feed_type", "feed_subscription_failed"),
    ],
)
def test_list_submission_statuses_maps_feed_subscription_outcomes(
    db_session: Session,
    content_factory,
    test_user,
    subscription_status: str,
    expected_outcome: str,
) -> None:
    content = content_factory(
        url=f"https://example.com/{subscription_status}",
        source_url=f"https://example.com/{subscription_status}",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.SKIPPED.value,
        title="Example Feed Request",
        content_metadata={
            "processing": {
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
                    "status": subscription_status,
                    "feed_url": "https://example.com/feed.xml",
                    "feed_type": "atom",
                    "created": subscription_status == "created",
                    "config_id": 42 if subscription_status == "created" else None,
                    "initial_download": {
                        "requested_count": 2,
                        "ran": subscription_status == "created",
                        "status": "completed" if subscription_status == "created" else "skipped",
                        "saved": 3 if subscription_status == "created" else 0,
                    },
                },
            }
        },
    )
    task, _ = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_FEED,
        url=f"https://example.com/{subscription_status}",
        created_at=datetime(2026, 6, 28, 12, 0, 0),
        action_name="subscribe_to_feed",
        action_result={"content_id": content.id},
    )

    response = list_submission_statuses.execute(
        db_session,
        user_id=test_user.id,
        cursor=None,
        limit=10,
    )

    item = next(item for item in response.submissions if item.id == task.id)
    assert item.submission_kind == "feed_subscription"
    assert item.outcome == expected_outcome
    assert item.detected_feed is not None
    assert item.detected_feed.url == "https://example.com/feed.xml"
    assert item.feed_subscription is not None
    assert item.feed_subscription.status == subscription_status
    assert item.feed_subscription.initial_download is not None
    assert item.feed_subscription.initial_download.requested_count == 2


@pytest.mark.parametrize(
    ("task_status", "retry_count", "expected_status", "expected_ran", "expected_error"),
    [
        (TaskStatus.PENDING, 0, "queued", False, None),
        (TaskStatus.PENDING, 1, "queued", True, None),
        (TaskStatus.PROCESSING, 0, "processing", True, None),
        (TaskStatus.COMPLETED, 0, "completed", True, None),
        (TaskStatus.FAILED, 3, "failed", True, "Initial download failed"),
    ],
)
def test_list_submission_statuses_reconciles_owned_initial_download_task(
    db_session: Session,
    content_factory,
    processing_task_factory,
    test_user,
    task_status: TaskStatus,
    retry_count: int,
    expected_status: str,
    expected_ran: bool,
    expected_error: str | None,
) -> None:
    now = datetime(2026, 6, 28, 12, 0, 0)
    lease_fields = (
        {
            "locked_at": now,
            "locked_by": "submission-status-test",
            "lease_token": uuid4(),
            "lease_expires_at": now + timedelta(minutes=1),
        }
        if task_status == TaskStatus.PROCESSING
        else {}
    )
    backfill_task = processing_task_factory(
        owner_user_id=test_user.id,
        task_type=TaskType.BACKFILL_FEEDS,
        payload={"user_id": test_user.id, "config_ids": [42], "count": 2},
        status=task_status,
        queue_name="backfill",
        error_message="internal provider detail",
        retry_count=retry_count,
        **lease_fields,
    )
    content = content_factory(
        url=f"https://example.com/reconciled-{task_status.value}",
        source_url=f"https://example.com/reconciled-{task_status.value}",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.SKIPPED.value,
        content_metadata={
            "processing": {
                "submitted_by_user_id": test_user.id,
                "submitted_via": "share_action",
                "subscribe_to_feed": True,
                "detected_feed": {
                    "url": "https://example.com/feed.xml",
                    "type": "atom",
                },
                "feed_subscription": {
                    "status": "created",
                    "initial_download": {
                        "task_id": backfill_task.id,
                        "requested_count": 2,
                        "ran": False,
                        "status": "queued",
                    },
                },
            }
        },
    )
    share_task, _ = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_FEED,
        url=content.url,
        created_at=now,
        action_name="subscribe_to_feed",
        action_result={"content_id": content.id},
    )

    response = list_submission_statuses.execute(
        db_session,
        user_id=test_user.id,
        cursor=None,
        limit=10,
    )

    item = next(item for item in response.submissions if item.id == share_task.id)
    assert item.feed_subscription is not None
    initial_download = item.feed_subscription.initial_download
    assert initial_download is not None
    assert initial_download.status == expected_status
    assert initial_download.ran is expected_ran
    assert initial_download.error == expected_error


def test_list_submission_statuses_does_not_project_another_users_backfill_task(
    db_session: Session,
    content_factory,
    processing_task_factory,
    test_user,
    user_factory,
) -> None:
    other_user = user_factory()
    foreign_task = processing_task_factory(
        owner_user_id=other_user.id,
        task_type=TaskType.BACKFILL_FEEDS,
        payload={"user_id": other_user.id, "config_ids": [42], "count": 2},
        status=TaskStatus.FAILED,
        queue_name="backfill",
        error_message="private foreign error",
    )
    content = content_factory(
        url="https://example.com/foreign-backfill-reference",
        source_url="https://example.com/foreign-backfill-reference",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.SKIPPED.value,
        content_metadata={
            "processing": {
                "submitted_by_user_id": test_user.id,
                "submitted_via": "share_action",
                "subscribe_to_feed": True,
                "feed_subscription": {
                    "status": "created",
                    "initial_download": {
                        "task_id": foreign_task.id,
                        "requested_count": 2,
                        "ran": False,
                        "status": "queued",
                    },
                },
            }
        },
    )
    share_task, _ = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_FEED,
        url=content.url,
        created_at=datetime(2026, 6, 28, 12, 0, 0),
        action_name="subscribe_to_feed",
        action_result={"content_id": content.id},
    )

    response = list_submission_statuses.execute(
        db_session,
        user_id=test_user.id,
        cursor=None,
        limit=10,
    )

    item = next(item for item in response.submissions if item.id == share_task.id)
    assert item.feed_subscription is not None
    initial_download = item.feed_subscription.initial_download
    assert initial_download is not None
    assert initial_download.status == "unavailable"
    assert initial_download.ran is None
    assert initial_download.error == "Initial download status is no longer available."


def test_list_submission_statuses_marks_cleaned_initial_download_task_unavailable(
    db_session: Session,
    content_factory,
    processing_task_factory,
    test_user,
) -> None:
    cleaned_task = processing_task_factory(
        owner_user_id=test_user.id,
        task_type=TaskType.BACKFILL_FEEDS,
        payload={"user_id": test_user.id, "config_ids": [42], "count": 2},
        status=TaskStatus.COMPLETED,
        queue_name="backfill",
    )
    cleaned_task_id = cleaned_task.id
    db_session.delete(cleaned_task)
    db_session.commit()
    content = content_factory(
        url="https://example.com/cleaned-backfill-reference",
        source_url="https://example.com/cleaned-backfill-reference",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.SKIPPED.value,
        content_metadata={
            "processing": {
                "submitted_by_user_id": test_user.id,
                "submitted_via": "share_action",
                "subscribe_to_feed": True,
                "feed_subscription": {
                    "status": "created",
                    "initial_download": {
                        "task_id": cleaned_task_id,
                        "requested_count": 2,
                        "ran": False,
                        "status": "queued",
                    },
                },
            }
        },
    )
    share_task, _ = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_FEED,
        url=content.url,
        created_at=datetime(2026, 6, 28, 12, 0, 0),
        action_name="subscribe_to_feed",
        action_result={"content_id": content.id},
    )

    response = list_submission_statuses.execute(
        db_session,
        user_id=test_user.id,
        cursor=None,
        limit=10,
    )

    item = next(item for item in response.submissions if item.id == share_task.id)
    assert item.feed_subscription is not None
    initial_download = item.feed_subscription.initial_download
    assert initial_download is not None
    assert initial_download.status == "unavailable"
    assert initial_download.ran is None
    assert initial_download.error == "Initial download status is no longer available."


def test_list_submission_statuses_keeps_generic_skipped_content_as_skipped(
    db_session: Session,
    content_factory,
    test_user,
) -> None:
    content = content_factory(
        url="https://example.com/generic-skipped",
        source_url="https://example.com/generic-skipped",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.SKIPPED.value,
        title="Generic Skipped Item",
        content_metadata={
            "processing": {
                "submitted_by_user_id": test_user.id,
                "submitted_via": "share_action",
            }
        },
    )
    task, _ = _create_share_action_task(
        db_session,
        user_id=test_user.id,
        mode=LlmTaskMode.ADD_CONTENT,
        url="https://example.com/generic-skipped",
        created_at=datetime(2026, 6, 28, 12, 0, 0),
        action_name="add_content",
        action_result={"content_id": content.id},
    )

    response = list_submission_statuses.execute(
        db_session,
        user_id=test_user.id,
        cursor=None,
        limit=10,
    )

    item = next(item for item in response.submissions if item.id == task.id)
    assert item.submission_kind == "content"
    assert item.outcome == "skipped"
    assert item.detected_feed is None
    assert item.feed_subscription is None


def _create_learning_deck_submission(
    db_session: Session,
    *,
    user_id: int,
    source_identity: str,
    source_url: str,
    title: str,
    status: LearningDeckRunStatus,
    created_at: datetime,
    source_metadata: dict,
) -> tuple[LearningDeck, LearningDeckRun]:
    deck = LearningDeck(
        user_id=user_id,
        source_kind=LearningDeckSourceKind.GITHUB_REPO.value,
        source_identity=source_identity,
        source_url=source_url,
        source_content_id=None,
        source_title=title,
        source_metadata=source_metadata,
        title=title,
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
        status=status.value,
        source_snapshot={
            "source_kind": LearningDeckSourceKind.GITHUB_REPO.value,
            "source_identity": source_identity,
            "source_url": source_url,
            "source_content_id": None,
            "source_title": title,
            "source_metadata": source_metadata,
        },
        timeline=[],
        artifact_object_keys=[],
        created_at=created_at,
        updated_at=created_at,
        completed_at=created_at if status == LearningDeckRunStatus.COMPLETED else None,
    )
    db_session.add(run)
    db_session.flush()
    deck.latest_run_id = run.id
    if status == LearningDeckRunStatus.COMPLETED:
        deck.latest_successful_run_id = run.id
    db_session.commit()
    db_session.refresh(deck)
    db_session.refresh(run)
    return deck, run


def _create_share_action_task(
    db_session: Session,
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
    output_json: dict | None = None,
    error_message: str | None = None,
) -> tuple[LlmTask, LlmTaskAction | None]:
    completed_at = (
        created_at
        if status
        in {
            LlmTaskStatus.COMPLETED,
            LlmTaskStatus.FAILED,
            LlmTaskStatus.CANCELLED,
        }
        else None
    )
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
        output_json=output_json or {},
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
    action = None
    if action_name is not None:
        action = LlmTaskAction(
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
        db_session.add(action)
    db_session.commit()
    db_session.refresh(task)
    if action is not None:
        db_session.refresh(action)
    return task, action
