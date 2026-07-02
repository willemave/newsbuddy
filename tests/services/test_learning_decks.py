from __future__ import annotations

import pytest

from app.models.contracts import (
    ContentType,
    LearningDeckSourceKind,
    LlmTaskKind,
    LlmTaskMode,
    LlmTaskStatus,
    TaskType,
)
from app.models.db import LearningDeck, LearningDeckRun, LlmTask, ProcessingTask
from app.services.learning_deck_viewer import with_learning_deck_navigation_controls
from app.services.learning_decks import (
    build_private_learning_deck_token,
    create_or_rerun_learning_deck,
    delete_learning_deck,
    disable_learning_deck_share,
    enable_learning_deck_share,
    get_deck_by_private_token,
    get_deck_by_valid_share_token,
    normalize_github_repository_source,
    present_learning_deck,
)
from tests.support.builders import create_content_status_entry_row


def _required_id(value: int | None) -> int:
    assert value is not None
    return value


def test_learning_deck_host_wrapper_adds_mobile_fit_controls() -> None:
    html = b"""<!doctype html>
<html>
<body>
<div class="reveal"><div class="slides"><section>Deck</section></div></div>
<script>Reveal.initialize({ width: 1280, height: 720 });</script>
</body>
</html>"""

    wrapped = with_learning_deck_navigation_controls(html).decode()

    assert "newsly-learning-deck-controls" in wrapped
    assert "newsly-learning-deck-portrait" in wrapped
    assert "visualViewport" in wrapped
    assert "width: 390, height: 720" not in wrapped
    assert "width: 1280" in wrapped
    assert "canvasHeight = isPhoneSized && !isPortrait ? 860 : 720" in wrapped
    assert "event.preventDefault()" in wrapped
    assert "availableRoutes" in wrapped


def _create_visible_article(db_session, test_user, content_factory):
    content = content_factory(
        content_type=ContentType.ARTICLE,
        title="Learning Systems",
        content_metadata={"content": "Source body about learning systems and architecture."},
    )
    create_content_status_entry_row(db_session, user=test_user, content=content)
    return content


def test_normalize_github_repository_source() -> None:
    source = normalize_github_repository_source("https://github.com/OpenAI/codex/tree/main")

    assert source is not None
    assert source.source_identity == "github:openai/codex"
    assert source.source_url == "https://github.com/OpenAI/codex"
    assert source.source_metadata == {"owner": "OpenAI", "repo": "codex"}


def test_normalize_github_blob_pdf_source_preserves_linked_artifact() -> None:
    source = normalize_github_repository_source(
        "https://github.com/deepseek-ai/DeepSpec/blob/main/DSpark_paper.pdf"
    )

    assert source is not None
    assert source.source_kind == LearningDeckSourceKind.GITHUB_REPO
    assert source.source_identity == "github:deepseek-ai/deepspec:file:main/DSpark_paper.pdf"
    assert source.source_url == (
        "https://github.com/deepseek-ai/DeepSpec/blob/main/DSpark_paper.pdf"
    )
    assert source.source_title == "deepseek-ai/DeepSpec: DSpark_paper.pdf"
    assert source.source_content_id is None
    assert source.source_metadata["repo_url"] == "https://github.com/deepseek-ai/DeepSpec"
    assert source.source_metadata["linked_artifact"] == {
        "url": "https://github.com/deepseek-ai/DeepSpec/blob/main/DSpark_paper.pdf",
        "raw_url": ("https://raw.githubusercontent.com/deepseek-ai/DeepSpec/main/DSpark_paper.pdf"),
        "path": "DSpark_paper.pdf",
        "filename": "DSpark_paper.pdf",
        "ref": "main",
        "content_type": "pdf",
    }


def test_create_learning_deck_from_content_enqueues_generation(
    db_session,
    test_user,
    content_factory,
) -> None:
    content = _create_visible_article(db_session, test_user, content_factory)

    deck = create_or_rerun_learning_deck(
        db_session,
        current_user=test_user,
        content_id=content.id,
        interests_prompt="Focus on architecture",
    )

    assert deck.source_identity == f"content:{content.id}"
    run = db_session.query(LearningDeckRun).filter_by(deck_id=deck.id).one()
    assert run.status == "queued"
    assert run.interests_prompt == "Focus on architecture"
    assert run.llm_task_id is not None
    llm_task = db_session.query(LlmTask).filter_by(id=run.llm_task_id).one()
    assert llm_task.task_kind == LlmTaskKind.LEARNING_DECK.value
    assert llm_task.mode == LlmTaskMode.LEARNING_DECK_PRESENTATION.value
    assert llm_task.status == LlmTaskStatus.QUEUED.value
    assert llm_task.workflow_key == "learning_deck.presentation.v1"
    assert llm_task.input_json["learning_deck_run_id"] == run.id
    task = db_session.query(ProcessingTask).one()
    assert task.task_type == TaskType.GENERATE_LEARNING_DECK.value
    assert task.queue_name == "learning"
    assert task.payload == {"learning_deck_run_id": run.id, "user_id": test_user.id}


def test_content_learning_deck_uses_resolved_pdf_title(
    db_session,
    test_user,
    content_factory,
) -> None:
    content = content_factory(
        content_type=ContentType.ARTICLE,
        title="https://arxiv.org/pdf/1706.03762",
        content_metadata={
            "content": "Transformer paper body.",
            "source_metadata": {"title": "Attention Is All You Need"},
        },
    )
    create_content_status_entry_row(db_session, user=test_user, content=content)

    deck = create_or_rerun_learning_deck(
        db_session,
        current_user=test_user,
        content_id=content.id,
    )

    assert deck.title == "Attention Is All You Need"
    response = present_learning_deck(db_session, deck)
    assert response.title == "Attention Is All You Need"
    assert response.source_title == "Attention Is All You Need"


def test_present_learning_deck_repairs_stale_url_title_from_source_metadata(
    db_session,
    test_user,
    content_factory,
) -> None:
    content = content_factory(
        content_type=ContentType.ARTICLE,
        title="Published as a conference paper at ICLR 2024",
        content_metadata={"content": "Paper body."},
    )
    create_content_status_entry_row(db_session, user=test_user, content=content)
    deck = create_or_rerun_learning_deck(
        db_session,
        current_user=test_user,
        content_id=content.id,
    )
    deck.title = "https://arxiv.org/pdf/2310.00785v4"
    deck.source_title = "https://arxiv.org/pdf/2310.00785v4"
    deck.source_metadata = {
        "paper_title": "BooookScore: A systematic exploration of book-length summarization"
    }
    db_session.commit()

    response = present_learning_deck(db_session, deck)

    assert response.title == "BooookScore: A systematic exploration of book-length summarization"
    assert response.source_title == response.title


def test_create_learning_deck_reuses_source_identity_for_rerun(
    db_session,
    test_user,
    content_factory,
) -> None:
    content = _create_visible_article(db_session, test_user, content_factory)
    first = create_or_rerun_learning_deck(
        db_session,
        current_user=test_user,
        content_id=content.id,
    )
    first_run = db_session.query(LearningDeckRun).filter_by(deck_id=first.id).one()
    first_run.status = "completed"
    first.latest_successful_run_id = first_run.id
    db_session.commit()

    second = create_or_rerun_learning_deck(
        db_session,
        current_user=test_user,
        content_id=content.id,
        interests_prompt="Now focus on alternatives",
    )

    assert second.id == first.id
    assert db_session.query(LearningDeck).count() == 1
    assert db_session.query(LearningDeckRun).count() == 2
    assert second.latest_successful_run_id == first_run.id


def test_create_learning_deck_enforces_one_active_run_per_user(
    db_session,
    test_user,
    content_factory,
) -> None:
    first_content = _create_visible_article(db_session, test_user, content_factory)
    second_content = content_factory(
        content_type=ContentType.ARTICLE,
        title="Second source",
        content_metadata={"content": "Second source body."},
    )
    create_content_status_entry_row(db_session, user=test_user, content=second_content)
    create_or_rerun_learning_deck(db_session, current_user=test_user, content_id=first_content.id)

    with pytest.raises(ValueError, match="already generating"):
        create_or_rerun_learning_deck(
            db_session,
            current_user=test_user,
            content_id=second_content.id,
        )


def test_share_tokens_enable_disable_and_private_tokens(
    db_session,
    test_user,
    content_factory,
) -> None:
    content = _create_visible_article(db_session, test_user, content_factory)
    deck = create_or_rerun_learning_deck(db_session, current_user=test_user, content_id=content.id)
    run = db_session.query(LearningDeckRun).filter_by(deck_id=deck.id).one()
    run.status = "completed"
    deck.latest_successful_run_id = run.id
    deck.deck_object_key = "learning/test/index.html"
    db_session.commit()

    deck_id = _required_id(deck.id)
    user_id = _required_id(test_user.id)
    share_token = enable_learning_deck_share(db_session, user_id=user_id, deck_id=deck_id)
    shared = get_deck_by_valid_share_token(db_session, token=share_token)
    assert shared.id == deck.id

    private_token, _expires_at = build_private_learning_deck_token(deck=deck, user_id=user_id)
    private = get_deck_by_private_token(db_session, token=private_token)
    assert private.id == deck.id

    disable_learning_deck_share(db_session, user_id=user_id, deck_id=deck_id)
    with pytest.raises(ValueError, match="not available"):
        get_deck_by_valid_share_token(db_session, token=share_token)


def test_delete_learning_deck_invalidates_access_and_deletes_artifacts(
    db_session,
    test_user,
    content_factory,
    monkeypatch,
) -> None:
    deleted_keys: list[str] = []
    content = _create_visible_article(db_session, test_user, content_factory)
    deck = create_or_rerun_learning_deck(db_session, current_user=test_user, content_id=content.id)
    run = db_session.query(LearningDeckRun).filter_by(deck_id=deck.id).one()
    run.artifact_object_keys = ["learning/run/index.html"]
    run.agent_log_object_key = "learning/internal/agent-log.jsonl"
    deck.artifact_object_keys = ["learning/deck/index.html"]
    db_session.commit()

    monkeypatch.setattr(
        "app.services.learning_decks.delete_learning_deck_objects",
        lambda keys: deleted_keys.extend(keys),
    )

    delete_learning_deck(
        db_session,
        user_id=_required_id(test_user.id),
        deck_id=_required_id(deck.id),
    )

    db_session.refresh(deck)
    assert deck.deleted_at is not None
    assert set(deleted_keys) == {"learning/deck/index.html", "learning/run/index.html"}
    assert "learning/internal/agent-log.jsonl" not in deleted_keys
