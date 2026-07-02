from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.api.share_actions import ShareActionAgentResult, ShareActionCreateRequest
from app.models.contracts import (
    LlmTaskActionStatus,
    LlmTaskApprovalPolicy,
    LlmTaskMode,
    LlmTaskStatus,
    TaskType,
)
from app.models.db import (
    Content,
    ContentKnowledgeSave,
    ContentReadStatus,
    LearningDeck,
    LlmTask,
    LlmTaskAction,
    ProcessingTask,
)
from app.services.llm_tasks import LlmTaskError
from app.services.share_action_agent import ShareActionAgentRunResult
from app.services.share_actions import create_share_action, run_share_action_task


def _share_request(**values: object) -> ShareActionCreateRequest:
    return ShareActionCreateRequest.model_validate(values)


def _agent_result(**values: object) -> ShareActionAgentResult:
    return ShareActionAgentResult.model_validate(values)


def _fake_agent_result(result: ShareActionAgentResult):
    def _runner(_db: Session, _task: LlmTask) -> ShareActionAgentRunResult:
        return ShareActionAgentRunResult(
            result=result,
            model_provider="openai",
            model_name="openai:gpt-test",
            sandbox_provider="local",
            sandbox_id="sandbox-test",
            agent_log_events=[],
        )

    return _runner


def test_create_share_action_enqueues_generic_llm_task(db_session, test_user) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/feed-page",
            mode=LlmTaskMode.ADD_FEED,
        ),
    )

    task = db_session.query(LlmTask).filter_by(id=response.task_id).one()
    assert task.task_kind == "share_action"
    assert task.mode == LlmTaskMode.ADD_FEED.value
    assert task.workflow_key == "share_action.add_feed.v1"
    assert task.allowed_actions == ["subscribe_to_feed"]
    queued = db_session.query(ProcessingTask).filter_by(task_type=TaskType.RUN_LLM_TASK.value).one()
    assert queued.queue_name == "llm"
    assert queued.payload == {"llm_task_id": task.id, "user_id": test_user.id}


def test_create_share_action_stores_save_and_read_preference(db_session, test_user) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/link-list",
            mode=LlmTaskMode.ADD_LINKS,
            save_to_knowledge_and_mark_read=True,
        ),
    )

    task = db_session.query(LlmTask).filter_by(id=response.task_id).one()
    assert task.input_json["save_to_knowledge_and_mark_read"] is True


def test_run_share_action_applies_auto_approved_add_content(db_session, test_user) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/story",
            mode=LlmTaskMode.ADD_CONTENT,
        ),
    )

    run_share_action_task(
        db_session,
        llm_task_id=response.task_id,
        agent_runner=_fake_agent_result(
            _agent_result(
                action="add_content",
                primary_url="https://example.com/story",
                title="Example Story",
                content_type="article",
                confidence=0.9,
            )
        ),
    )

    task = db_session.query(LlmTask).filter_by(id=response.task_id).one()
    assert task.status == LlmTaskStatus.COMPLETED.value
    action = db_session.query(LlmTaskAction).filter_by(llm_task_id=task.id).one()
    assert action.action_status == LlmTaskActionStatus.APPLIED.value
    content = db_session.query(Content).filter_by(url="https://example.com/story").one()
    assert content.title == "Example Story"


def test_run_share_action_add_links_preserves_save_and_read_preference(
    db_session,
    test_user,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/link-list",
            mode=LlmTaskMode.ADD_LINKS,
            save_to_knowledge_and_mark_read=True,
        ),
    )

    run_share_action_task(
        db_session,
        llm_task_id=response.task_id,
        agent_runner=_fake_agent_result(
            _agent_result(
                action="add_links",
                primary_url="https://example.com/link-list",
                content_urls=[
                    {
                        "url": "https://example.com/linked-story",
                        "title": "Linked Story",
                    }
                ],
                confidence=0.8,
            )
        ),
    )

    content = db_session.query(Content).filter_by(url="https://example.com/linked-story").one()
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=test_user.id, content_id=content.id)
        .one_or_none()
        is not None
    )
    assert (
        db_session.query(ContentReadStatus)
        .filter_by(user_id=test_user.id, content_id=content.id)
        .one_or_none()
        is not None
    )


def test_run_share_action_add_links_uses_bounded_idempotency_key(
    db_session,
    test_user,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/link-list",
            mode=LlmTaskMode.ADD_LINKS,
            save_to_knowledge_and_mark_read=True,
        ),
    )

    run_share_action_task(
        db_session,
        llm_task_id=response.task_id,
        agent_runner=_fake_agent_result(
            _agent_result(
                action="add_links",
                primary_url="https://example.com/link-list",
                content_urls=[
                    {
                        "url": f"https://example.com/linked-story-{index}",
                        "title": f"Linked Story {index}",
                        "rationale": "substantive candidate " * 40,
                    }
                    for index in range(30)
                ],
                confidence=0.8,
            )
        ),
    )

    action = db_session.query(LlmTaskAction).filter_by(llm_task_id=response.task_id).one()
    assert action.action_status == LlmTaskActionStatus.APPLIED.value
    assert action.idempotency_key is not None
    assert len(action.idempotency_key) <= 512


def test_run_share_action_chat_saves_content_to_knowledge(db_session, test_user) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/chat-target",
            mode=LlmTaskMode.CHAT,
            chat_initial_message="Help me use this later.",
        ),
    )

    run_share_action_task(
        db_session,
        llm_task_id=response.task_id,
        agent_runner=_fake_agent_result(
            _agent_result(
                action="chat",
                primary_url="https://example.com/chat-target",
                confidence=0.8,
            )
        ),
    )

    content = db_session.query(Content).filter_by(url="https://example.com/chat-target").one()
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=test_user.id, content_id=content.id)
        .one_or_none()
        is not None
    )


def test_run_share_action_presentation_marks_learning_deck_submission(
    db_session,
    test_user,
) -> None:
    blob_url = "https://github.com/deepseek-ai/DeepSpec/blob/main/DSpark_paper.pdf"
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url=blob_url,
            mode=LlmTaskMode.PRESENTATION,
            interests_prompt="Focus on proof strategy.",
        ),
    )

    run_share_action_task(
        db_session,
        llm_task_id=response.task_id,
        agent_runner=_fake_agent_result(
            _agent_result(
                action="presentation",
                primary_url=blob_url,
                title="DSpark paper",
                confidence=0.9,
            )
        ),
    )

    deck = db_session.query(LearningDeck).one()
    assert deck.source_url == blob_url
    assert deck.source_content_id is None
    assert deck.source_title == "deepseek-ai/DeepSpec: DSpark_paper.pdf"
    linked_artifact = (deck.source_metadata or {}).get("linked_artifact")
    assert isinstance(linked_artifact, dict)
    assert linked_artifact["raw_url"] == (
        "https://raw.githubusercontent.com/deepseek-ai/DeepSpec/main/DSpark_paper.pdf"
    )
    submission_metadata = (deck.source_metadata or {}).get("submission")
    assert submission_metadata == {
        "submitted_via": "share_action",
        "share_action_task_id": response.task_id,
    }
    learning_deck_task = db_session.query(LlmTask).filter_by(task_kind="learning_deck").one()
    source_snapshot = learning_deck_task.input_json["source"]
    assert source_snapshot["source_kind"] == "github_repo"
    assert source_snapshot["source_content_id"] is None
    assert source_snapshot["source_metadata"]["linked_artifact"]["path"] == "DSpark_paper.pdf"
    assert db_session.query(Content).count() == 0
    action = db_session.query(LlmTaskAction).filter_by(llm_task_id=response.task_id).one()
    assert action.action_result["learning_deck_id"] == deck.id


def test_run_share_action_waits_for_approval_when_policy_requires_it(
    db_session,
    test_user,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/feed-page",
            mode=LlmTaskMode.ADD_FEED,
            approval_policy={"default": LlmTaskApprovalPolicy.APPROVAL_REQUIRED.value},
        ),
    )

    run_share_action_task(
        db_session,
        llm_task_id=response.task_id,
        agent_runner=_fake_agent_result(
            _agent_result(
                action="add_feed",
                primary_url="https://example.com/feed-page",
                feed_url="https://example.com/feed.xml",
                confidence=0.9,
            )
        ),
    )

    task = db_session.query(LlmTask).filter_by(id=response.task_id).one()
    assert task.status == LlmTaskStatus.AWAITING_APPROVAL.value
    action = db_session.query(LlmTaskAction).filter_by(llm_task_id=task.id).one()
    assert action.action_status == LlmTaskActionStatus.AWAITING_APPROVAL.value
    assert action.action_input["url"] == "https://example.com/feed.xml"
    assert db_session.query(Content).filter_by(url="https://example.com/feed.xml").count() == 0


def test_run_share_action_rejects_result_action_that_does_not_match_mode(
    db_session,
    test_user,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/feed-page",
            mode=LlmTaskMode.ADD_FEED,
        ),
    )

    with pytest.raises(LlmTaskError, match="does not match mode"):
        run_share_action_task(
            db_session,
            llm_task_id=response.task_id,
            agent_runner=_fake_agent_result(
                _agent_result(
                    action="chat",
                    primary_url="https://example.com/feed-page",
                    confidence=0.9,
                )
            ),
        )

    task = db_session.query(LlmTask).filter_by(id=response.task_id).one()
    assert task.status == LlmTaskStatus.FAILED.value
    assert db_session.query(LlmTaskAction).filter_by(llm_task_id=task.id).count() == 0
