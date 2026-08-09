from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError
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
    ChatMessage,
    ChatSession,
    Content,
    ContentKnowledgeSave,
    ContentReadStatus,
    ContentStatusEntry,
    LearningDeck,
    LlmTask,
    LlmTaskAction,
    ProcessingTask,
)
from app.models.metadata.state import extract_share_and_chat_requests
from app.services import share_actions
from app.services.llm_tasks import (
    LlmTaskError,
    approve_llm_task_action,
    request_llm_task_action,
    set_llm_task_status,
)
from app.services.share_action_agent import ShareActionAgentRunResult
from app.services.share_actions import (
    apply_share_task_action,
    create_share_action,
    run_share_action_task,
)


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


def test_apply_share_task_action_failure_terminally_fails_action_and_task(
    db_session,
    test_user,
    monkeypatch,
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
    task = db_session.query(LlmTask).filter_by(id=response.task_id).one()
    action = request_llm_task_action(
        db_session,
        task=task,
        action_name="subscribe_to_feed",
        action_input={"url": "https://example.com/feed.xml"},
    )
    approve_llm_task_action(db_session, action=action, approved_by_user_id=test_user.id)
    set_llm_task_status(
        db_session,
        task,
        status=LlmTaskStatus.AWAITING_APPROVAL,
        workflow_state="awaiting_approval",
        note="Awaiting test approval",
    )
    db_session.commit()

    def fail_apply(*_args, **_kwargs):
        raise RuntimeError("feed application unavailable")

    monkeypatch.setattr(share_actions, "_apply_action", fail_apply)

    with pytest.raises(RuntimeError, match="feed application unavailable"):
        apply_share_task_action(db_session, task=task, action=action)

    db_session.expire_all()
    persisted_task = db_session.query(LlmTask).filter_by(id=task.id).one()
    persisted_action = db_session.query(LlmTaskAction).filter_by(id=action.id).one()
    assert persisted_action.action_status == LlmTaskActionStatus.FAILED.value
    assert persisted_action.error_message == "feed application unavailable"
    assert persisted_task.status == LlmTaskStatus.FAILED.value
    assert persisted_task.workflow_state == "failed"
    assert persisted_task.error_type == "RuntimeError"
    assert persisted_task.error_message == "feed application unavailable"


def test_apply_share_task_action_recovers_from_aborted_database_transaction(
    db_session,
    test_user,
    monkeypatch,
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
    task = db_session.query(LlmTask).filter_by(id=response.task_id).one()
    action = request_llm_task_action(
        db_session,
        task=task,
        action_name="subscribe_to_feed",
        action_input={"url": "https://example.com/feed.xml"},
    )
    approve_llm_task_action(db_session, action=action, approved_by_user_id=test_user.id)
    set_llm_task_status(
        db_session,
        task,
        status=LlmTaskStatus.AWAITING_APPROVAL,
        workflow_state="awaiting_approval",
        note="Awaiting test approval",
    )
    db_session.commit()
    task_id = task.id
    action_id = action.id

    def fail_apply(db, **_kwargs):
        db.execute(text("SELECT 1 / 0"))
        return {}

    monkeypatch.setattr(share_actions, "_apply_action", fail_apply)

    with pytest.raises(DataError, match="division by zero"):
        apply_share_task_action(db_session, task=task, action=action)

    db_session.expire_all()
    persisted_task = db_session.query(LlmTask).filter_by(id=task_id).one()
    persisted_action = db_session.query(LlmTaskAction).filter_by(id=action_id).one()
    assert persisted_action.action_status == LlmTaskActionStatus.FAILED.value
    assert persisted_action.error_message is not None
    assert "division by zero" in persisted_action.error_message
    assert persisted_task.status == LlmTaskStatus.FAILED.value
    assert persisted_task.workflow_state == "failed"
    assert persisted_task.error_type == "DataError"
    assert persisted_task.error_message is not None
    assert "division by zero" in persisted_task.error_message


def test_create_share_action_defers_source_ingestion_to_worker(
    db_session,
    test_user,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/presentation-source",
            mode=LlmTaskMode.PRESENTATION,
        ),
    )

    task = db_session.query(LlmTask).filter_by(id=response.task_id).one()
    assert "knowledge_content_id" not in task.input_json
    assert (
        db_session.query(Content).filter_by(url="https://example.com/presentation-source").count()
        == 0
    )
    assert db_session.query(ContentKnowledgeSave).count() == 0


def test_create_share_action_feed_does_not_save_source_to_knowledge(
    db_session,
    test_user,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/feed.xml",
            mode=LlmTaskMode.ADD_FEED,
        ),
    )

    task = db_session.query(LlmTask).filter_by(id=response.task_id).one()
    assert "knowledge_content_id" not in task.input_json
    assert db_session.query(Content).filter_by(url="https://example.com/feed.xml").count() == 0
    assert db_session.query(ContentKnowledgeSave).count() == 0


def test_run_add_to_briefing_feed_target_subscribes_without_ingesting_homepage(
    db_session,
    test_user,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/publication",
            mode=LlmTaskMode.ADD_TO_BRIEFING,
        ),
    )

    task = run_share_action_task(
        db_session,
        llm_task_id=response.task_id,
        agent_runner=_fake_agent_result(
            _agent_result(
                action="add_to_briefing",
                briefing_target={
                    "kind": "feed",
                    "url": "https://example.com/feed.xml",
                    "title": "Example Publication",
                    "rationale": "Validated source feed",
                },
            )
        ),
    )

    assert task.status == LlmTaskStatus.COMPLETED.value
    assert db_session.query(Content).filter_by(url="https://example.com/publication").count() == 0
    content = db_session.query(Content).filter_by(url="https://example.com/feed.xml").one()
    assert content.content_metadata["subscribe_to_feed"] is True
    assert db_session.query(ContentKnowledgeSave).filter_by(content_id=content.id).count() == 0
    action = db_session.query(LlmTaskAction).filter_by(llm_task_id=response.task_id).one()
    assert action.action_name == "add_to_briefing"
    assert action.action_result["resolved_kind"] == "feed"
    assert action.action_result["resolved_url"] == "https://example.com/feed.xml"


def test_run_add_to_briefing_content_target_uses_briefing_content_pipeline(
    db_session,
    test_user,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/tracked-story",
            mode=LlmTaskMode.ADD_TO_BRIEFING,
        ),
    )

    run_share_action_task(
        db_session,
        llm_task_id=response.task_id,
        agent_runner=_fake_agent_result(
            _agent_result(
                action="add_to_briefing",
                briefing_target={
                    "kind": "content",
                    "url": "https://example.com/canonical-story",
                    "title": "Canonical Story",
                    "content_type": "article",
                    "rationale": "Canonical individual article",
                },
            )
        ),
    )

    assert db_session.query(Content).filter_by(url="https://example.com/tracked-story").count() == 0
    content = db_session.query(Content).filter_by(url="https://example.com/canonical-story").one()
    assert not content.content_metadata.get("subscribe_to_feed")
    assert (
        db_session.query(ContentStatusEntry)
        .filter_by(user_id=test_user.id, content_id=content.id, status="inbox")
        .one_or_none()
        is not None
    )
    action = db_session.query(LlmTaskAction).filter_by(llm_task_id=response.task_id).one()
    assert action.action_result["resolved_kind"] == "content"


def test_run_add_to_briefing_no_action_creates_no_product_state(
    db_session,
    test_user,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/unsupported-homepage",
            mode=LlmTaskMode.ADD_TO_BRIEFING,
        ),
    )

    task = run_share_action_task(
        db_session,
        llm_task_id=response.task_id,
        agent_runner=_fake_agent_result(
            _agent_result(
                action="no_action",
                rationale="Neither a source nor an eligible item",
            )
        ),
    )

    assert task.status == LlmTaskStatus.COMPLETED.value
    assert task.output_json == {
        "action": "no_action",
        "primary_url": None,
        "feed_url": None,
        "content_urls": [],
        "presentation": None,
        "chat": None,
        "briefing_target": None,
        "title": None,
        "platform": None,
        "content_type": None,
        "rationale": "Neither a source nor an eligible item",
        "sources_used": [],
        "confidence": None,
    }
    assert db_session.query(Content).count() == 0
    assert db_session.query(LlmTaskAction).filter_by(llm_task_id=response.task_id).count() == 0


def test_run_add_to_briefing_rejects_missing_target_without_product_state(
    db_session,
    test_user,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/ambiguous",
            mode=LlmTaskMode.ADD_TO_BRIEFING,
        ),
    )

    with pytest.raises(LlmTaskError, match="missing briefing_target"):
        run_share_action_task(
            db_session,
            llm_task_id=response.task_id,
            agent_runner=_fake_agent_result(
                _agent_result(
                    action="add_to_briefing",
                    rationale="Ambiguous output",
                )
            ),
        )

    task = db_session.query(LlmTask).filter_by(id=response.task_id).one()
    assert task.status == LlmTaskStatus.FAILED.value
    assert db_session.query(Content).count() == 0


@pytest.mark.parametrize(
    "mode, expected",
    [
        (LlmTaskMode.ADD_CONTENT, True),
        (LlmTaskMode.ADD_LINKS, True),
        (LlmTaskMode.CHAT, True),
        (LlmTaskMode.PRESENTATION, True),
        (LlmTaskMode.BOOKMARK_ONLY, True),
        (LlmTaskMode.ADD_FEED, False),
        (LlmTaskMode.ADD_TO_BRIEFING, False),
    ],
)
def test_share_action_workflow_owns_source_save_policy(
    mode: LlmTaskMode,
    expected: bool,
) -> None:
    assert (
        share_actions.share_action_workflow_for_mode(mode).save_shared_source_to_knowledge
        is expected
    )


def test_run_share_action_records_source_preparation_failure(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/unavailable",
            mode=LlmTaskMode.ADD_CONTENT,
        ),
    )

    def fail_submission(*_args, **_kwargs) -> None:
        raise RuntimeError("ingestion unavailable")

    monkeypatch.setattr(share_actions, "_submit_content", fail_submission)

    with pytest.raises(RuntimeError, match="ingestion unavailable"):
        run_share_action_task(db_session, llm_task_id=response.task_id)

    task = db_session.query(LlmTask).filter_by(id=response.task_id).one()
    assert task.status == LlmTaskStatus.FAILED.value
    assert task.workflow_state == "failed"
    assert task.error_type == "RuntimeError"
    assert task.error_message == "ingestion unavailable"


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
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=test_user.id, content_id=content.id)
        .one_or_none()
        is not None
    )


def test_run_share_action_add_links_saves_results_to_knowledge(
    db_session,
    test_user,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/link-list",
            mode=LlmTaskMode.ADD_LINKS,
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


def test_run_share_action_add_links_persists_partial_result(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/partial-link-list",
            mode=LlmTaskMode.ADD_LINKS,
        ),
    )
    original_submit = share_actions._submit_content

    def _selective_submit(*args, **kwargs):
        if kwargs["action_input"].url.endswith("/bad"):
            raise ValueError("candidate rejected")
        return original_submit(*args, **kwargs)

    monkeypatch.setattr(share_actions, "_submit_content", _selective_submit)

    run_share_action_task(
        db_session,
        llm_task_id=response.task_id,
        agent_runner=_fake_agent_result(
            _agent_result(
                action="add_links",
                primary_url="https://example.com/partial-link-list",
                content_urls=[
                    {"url": "https://example.com/good"},
                    {"url": "https://example.com/bad"},
                ],
            )
        ),
    )

    action = db_session.query(LlmTaskAction).filter_by(llm_task_id=response.task_id).one()
    assert action.action_status == LlmTaskActionStatus.APPLIED.value
    assert action.action_result["outcome"] == "partial"
    assert action.action_result["attempted_count"] == 2
    assert action.action_result["succeeded_count"] == 1
    assert action.action_result["failed_count"] == 1
    assert action.action_result["items"][1] == {
        "url": "https://example.com/bad",
        "outcome": "failed",
        "error": "candidate rejected",
    }


def test_run_share_action_add_links_fails_when_no_candidate_applies(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/failed-link-list",
            mode=LlmTaskMode.ADD_LINKS,
        ),
    )
    original_submit = share_actions._submit_content

    def _reject_candidates(*args, **kwargs):
        if "/failed-candidate-" in kwargs["action_input"].url:
            raise ValueError("candidate rejected")
        return original_submit(*args, **kwargs)

    monkeypatch.setattr(share_actions, "_submit_content", _reject_candidates)

    with pytest.raises(LlmTaskError, match="All discovered links failed"):
        run_share_action_task(
            db_session,
            llm_task_id=response.task_id,
            agent_runner=_fake_agent_result(
                _agent_result(
                    action="add_links",
                    primary_url="https://example.com/failed-link-list",
                    content_urls=[
                        {"url": "https://example.com/failed-candidate-1"},
                        {"url": "https://example.com/failed-candidate-2"},
                    ],
                )
            ),
        )

    task = db_session.query(LlmTask).filter_by(id=response.task_id).one()
    action = db_session.query(LlmTaskAction).filter_by(llm_task_id=response.task_id).one()
    assert task.status == LlmTaskStatus.FAILED.value
    assert action.action_status == LlmTaskActionStatus.FAILED.value
    assert action.action_result["outcome"] == "failed"
    assert action.action_result["failed_count"] == 2


def test_run_share_action_chat_uses_content_pipeline_without_preprocessing_agent(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    response = create_share_action(
        db_session,
        current_user=test_user,
        payload=_share_request(
            url="https://example.com/chat-target",
            mode=LlmTaskMode.CHAT,
            chat_initial_message="Help me use this later.",
        ),
    )

    monkeypatch.setattr(
        share_actions,
        "_run_default_agent",
        lambda *_args, **_kwargs: pytest.fail("chat handoff must not start a VM agent"),
    )
    run_share_action_task(db_session, llm_task_id=response.task_id)

    content = db_session.query(Content).filter_by(url="https://example.com/chat-target").one()
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=test_user.id, content_id=content.id)
        .one_or_none()
        is not None
    )
    assert extract_share_and_chat_requests(content.content_metadata) == [
        {"user_id": test_user.id, "initial_message": "Help me use this later."}
    ]
    task = db_session.query(LlmTask).filter_by(id=response.task_id).one()
    assert task.model_provider is None
    assert task.model_name is None
    assert task.sandbox_provider is None
    assert task.output_json == {}
    action = db_session.query(LlmTaskAction).filter_by(llm_task_id=response.task_id).one()
    assert action.action_name == "enqueue_chat"
    assert action.action_status == LlmTaskActionStatus.APPLIED.value
    session = db_session.query(ChatSession).filter_by(content_id=content.id).one()
    assert session.session_type == "knowledge_chat"
    assert db_session.query(ChatMessage).filter_by(session_id=session.id).count() == 0
    assert action.action_result["chat_session_id"] == session.id


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
    content = db_session.query(Content).filter_by(url=blob_url).one()
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=test_user.id, content_id=content.id)
        .one_or_none()
        is not None
    )
    action = db_session.query(LlmTaskAction).filter_by(llm_task_id=response.task_id).one()
    assert action.action_result["learning_deck_id"] == deck.id
    assert action.action_result["content_id"] == content.id


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
