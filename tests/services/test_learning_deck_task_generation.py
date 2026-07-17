from __future__ import annotations

import pytest

from app.models.contracts import ContentStatus, ContentType, LlmTaskStatus
from app.models.db import LlmTask
from app.services.learning_deck_agent import LearningDeckAgentResult
from app.services.learning_deck_artifacts import StoredLearningDeckArtifact
from app.services.learning_deck_generation import LearningDeckGenerationWaiting
from app.services.learning_deck_task_generation import run_learning_deck_task
from app.services.learning_decks import create_or_rerun_learning_deck, present_learning_deck
from tests.support.builders import create_content_status_entry_row


def _create_task(db_session, test_user, content_factory):
    content = content_factory(
        content_type=ContentType.ARTICLE,
        title="Collective Communication",
        content_metadata={"content": "A sufficiently detailed source body."},
    )
    create_content_status_entry_row(db_session, user=test_user, content=content)
    deck = create_or_rerun_learning_deck(
        db_session,
        current_user=test_user,
        content_id=content.id,
        interests_prompt="Focus on topology",
    )
    task = db_session.query(LlmTask).filter_by(id=deck.latest_task_id).one()
    return content, deck, task


def test_learning_deck_llm_task_defers_while_source_is_processing(
    db_session,
    test_user,
    content_factory,
) -> None:
    content, deck, task = _create_task(db_session, test_user, content_factory)
    content.status = ContentStatus.PROCESSING.value
    db_session.commit()

    with pytest.raises(LearningDeckGenerationWaiting) as exc_info:
        run_learning_deck_task(db_session, llm_task_id=task.id)

    db_session.refresh(task)
    db_session.refresh(deck)
    assert task.status == LlmTaskStatus.PREPARING.value
    assert deck.latest_task_id == task.id
    assert exc_info.value.retry_delay_seconds >= 30


def test_learning_deck_llm_task_publishes_and_drives_api_projection(
    db_session,
    test_user,
    content_factory,
    monkeypatch,
) -> None:
    _content, deck, task = _create_task(db_session, test_user, content_factory)
    agent_result = LearningDeckAgentResult(
        index_html="<html>deck</html>",
        source_notes_md="# Sources\n\n- Source",
        assets={},
        model_provider="openai",
        model_name="test-model",
        sandbox_provider="local",
        sandbox_id="sandbox-1",
        source_metadata_updates={"inspected": True},
        agent_log_events=[],
    )
    stored = StoredLearningDeckArtifact(
        storage_prefix="learning/1/1",
        deck_object_key="learning/1/1/index.html",
        source_notes_object_key="learning/1/1/source-notes.md",
        source_notes_html_object_key="learning/1/1/source-notes.html",
        artifact_object_keys=["learning/1/1/index.html"],
    )
    monkeypatch.setattr(
        "app.services.learning_deck_task_generation.run_learning_deck_agent",
        lambda **_kwargs: agent_result,
    )
    monkeypatch.setattr(
        "app.services.learning_deck_task_generation.store_learning_deck_artifact",
        lambda **_kwargs: stored,
    )

    result = run_learning_deck_task(db_session, llm_task_id=task.id)

    db_session.refresh(deck)
    assert result.status == LlmTaskStatus.COMPLETED.value
    assert deck.latest_successful_task_id == task.id
    assert deck.deck_object_key == stored.deck_object_key
    response = present_learning_deck(db_session, deck)
    assert response.status == "ready"
    assert response.latest_run is not None
    assert response.latest_run.id == task.id
    assert response.latest_run.interests_prompt == "Focus on topology"
    assert response.viewer_available is True


def test_learning_deck_llm_task_refuses_to_publish_after_lease_loss(
    db_session,
    test_user,
    content_factory,
    monkeypatch,
) -> None:
    _content, _deck, task = _create_task(db_session, test_user, content_factory)
    monkeypatch.setattr(
        "app.services.learning_deck_task_generation.run_learning_deck_agent",
        lambda **_kwargs: LearningDeckAgentResult(
            index_html="<html>deck</html>",
            source_notes_md="# Sources\n\n- Source",
            assets={},
            model_provider="openai",
            model_name="test-model",
            sandbox_provider="local",
            sandbox_id="sandbox-1",
            source_metadata_updates={},
            agent_log_events=[],
        ),
    )
    stored = False

    def store(**_kwargs):
        nonlocal stored
        stored = True
        raise AssertionError("artifact storage must not run after lease loss")

    monkeypatch.setattr(
        "app.services.learning_deck_task_generation.store_learning_deck_artifact",
        store,
    )

    with pytest.raises(ValueError, match="lease was lost"):
        run_learning_deck_task(
            db_session,
            llm_task_id=task.id,
            ensure_lease=lambda: False,
        )

    db_session.refresh(task)
    assert stored is False
    assert task.status == LlmTaskStatus.FAILED.value
    assert task.error_type == "lease_lost"
