from __future__ import annotations

import pytest

from app.models.contracts import ContentStatus, ContentType
from app.models.db import LearningDeck, LearningDeckRun
from app.services.gateways.object_storage_gateway import LocalObjectStorageGateway
from app.services.learning_deck_agent import (
    LearningDeckAgentExecutionError,
    LearningDeckAgentResult,
)
from app.services.learning_deck_generation import (
    LearningDeckGenerationWaiting,
    generate_learning_deck,
)
from app.services.learning_decks import promote_learning_deck_run
from tests.support.builders import create_content_status_entry_row


def _required_id(value: int | None) -> int:
    assert value is not None
    return value


VALID_INDEX_HTML = """<!doctype html>
<html>
<head>
  <link rel="stylesheet" href="assets/theme.css">
</head>
<body>
  <div class="reveal"><div class="slides"><section>Learning Deck</section></div></div>
  <script src="https://cdn.jsdelivr.net/npm/reveal.js/dist/reveal.js"></script>
  <script>Reveal.initialize();</script>
</body>
</html>
"""

VALID_SOURCE_NOTES = """# Source Notes

## Sources

- Primary source: fixture article.

## Source-to-slide mapping

- Slide 1 uses the fixture article.
"""


def _create_run(db_session, test_user, content_factory):
    content = content_factory(
        content_type=ContentType.ARTICLE,
        title="Deck Source",
        content_metadata={"content": "Source body for a generated learning deck."},
    )
    create_content_status_entry_row(db_session, user=test_user, content=content)
    deck = LearningDeck(
        user_id=test_user.id,
        source_kind="content",
        source_identity=f"content:{content.id}",
        source_url=content.url,
        source_content_id=content.id,
        source_title=content.title,
        source_metadata={"content_type": "article"},
        title=content.title,
        artifact_object_keys=[],
    )
    db_session.add(deck)
    db_session.flush()
    run = LearningDeckRun(
        deck_id=deck.id,
        user_id=test_user.id,
        status="queued",
        interests_prompt="Focus on systems",
        source_snapshot={},
        timeline=[],
        artifact_object_keys=[],
    )
    db_session.add(run)
    db_session.flush()
    deck.latest_run_id = run.id
    db_session.commit()
    return deck, run


def test_generate_learning_deck_promotes_successful_artifact(
    db_session,
    test_user,
    content_factory,
    tmp_path,
    monkeypatch,
) -> None:
    gateway = LocalObjectStorageGateway(tmp_path)
    monkeypatch.setattr(
        "app.services.learning_deck_artifacts.get_object_storage_gateway",
        lambda: gateway,
    )
    deck, run = _create_run(db_session, test_user, content_factory)
    expected_run_id = _required_id(run.id)
    expected_user_id = _required_id(test_user.id)

    def fake_runner(source_snapshot, interests_prompt, user_id, run_id):
        assert source_snapshot["body_text"].startswith("Source body")
        assert interests_prompt == "Focus on systems"
        assert user_id == expected_user_id
        assert run_id == expected_run_id
        return LearningDeckAgentResult(
            index_html=VALID_INDEX_HTML,
            source_notes_md=VALID_SOURCE_NOTES,
            assets={"assets/theme.css": (b".reveal { color: black; }", "text/css")},
            model_provider="openai",
            model_name="openai:test",
            sandbox_provider="e2b",
            sandbox_id="sandbox-1",
            source_metadata_updates={"default_branch": "main"},
            agent_log_events=[
                {
                    "event_type": "bash",
                    "payload": {"command": "echo build"},
                }
            ],
        )

    generate_learning_deck(
        db_session,
        learning_deck_run_id=expected_run_id,
        agent_runner=fake_runner,
    )

    db_session.refresh(run)
    db_session.refresh(deck)
    assert run.status == "completed"
    assert deck.latest_successful_run_id == run.id
    assert deck.deck_object_key
    assert gateway.exists(key=deck.deck_object_key)
    assert gateway.exists(key=f"{deck.artifact_storage_prefix}/assets/theme.css")
    assert run.agent_log_object_key
    assert gateway.exists(key=run.agent_log_object_key)
    assert b"echo build" in gateway.get_bytes(key=run.agent_log_object_key)
    assert isinstance(deck.source_metadata, dict)
    assert deck.source_metadata["default_branch"] == "main"


def test_failed_rerun_preserves_previous_successful_artifact(
    db_session,
    test_user,
    content_factory,
    tmp_path,
    monkeypatch,
) -> None:
    gateway = LocalObjectStorageGateway(tmp_path)
    monkeypatch.setattr(
        "app.services.learning_deck_artifacts.get_object_storage_gateway",
        lambda: gateway,
    )
    deck, old_run = _create_run(db_session, test_user, content_factory)
    old_run.status = "completed"
    old_run.deck_object_key = "learning/old/index.html"
    old_run.artifact_object_keys = ["learning/old/index.html"]
    deck.latest_successful_run_id = old_run.id
    deck.deck_object_key = old_run.deck_object_key
    deck.artifact_object_keys = list(old_run.artifact_object_keys)
    new_run = LearningDeckRun(
        deck_id=deck.id,
        user_id=test_user.id,
        status="queued",
        source_snapshot={},
        timeline=[],
        artifact_object_keys=[],
    )
    db_session.add(new_run)
    db_session.flush()
    deck.latest_run_id = new_run.id
    db_session.commit()

    def fake_runner(_source_snapshot, _interests_prompt, _user_id, _run_id):
        return LearningDeckAgentResult(
            index_html=VALID_INDEX_HTML.replace(
                "https://cdn.jsdelivr.net/npm/reveal.js/dist/reveal.js",
                "https://evil.example/script.js",
            ),
            source_notes_md=VALID_SOURCE_NOTES,
            assets={},
            model_provider="openai",
            model_name="openai:test",
            sandbox_provider="e2b",
            sandbox_id="sandbox-2",
            source_metadata_updates={},
        )

    with pytest.raises(ValueError, match="disallowed script source"):
        generate_learning_deck(
            db_session,
            learning_deck_run_id=_required_id(new_run.id),
            agent_runner=fake_runner,
        )

    db_session.refresh(deck)
    db_session.refresh(new_run)
    assert new_run.status == "failed"
    assert deck.latest_successful_run_id == old_run.id
    assert deck.deck_object_key == "learning/old/index.html"


def test_promote_learning_deck_run_does_not_delete_new_artifact_keys(
    db_session,
    test_user,
    content_factory,
    monkeypatch,
) -> None:
    deleted_keys: list[str] = []
    deck, run = _create_run(db_session, test_user, content_factory)
    deck.artifact_object_keys = [
        "learning/decks/1/runs/old/index.html",
        "learning/decks/1/runs/22/index.html",
    ]
    db_session.commit()

    monkeypatch.setattr(
        "app.services.learning_decks.delete_learning_deck_objects",
        lambda keys: deleted_keys.extend(keys),
    )

    promote_learning_deck_run(
        db_session,
        run,
        artifact_storage_prefix="learning/decks/1/runs/22",
        deck_object_key="learning/decks/1/runs/22/index.html",
        source_notes_object_key="learning/decks/1/runs/22/source-notes.md",
        source_notes_html_object_key="learning/decks/1/runs/22/source-notes.html",
        artifact_object_keys=[
            "learning/decks/1/runs/22/index.html",
            "learning/decks/1/runs/22/source-notes.md",
            "learning/decks/1/runs/22/source-notes.html",
        ],
    )

    assert deleted_keys == ["learning/decks/1/runs/old/index.html"]


def test_generate_learning_deck_waits_for_unprocessed_source(
    db_session,
    test_user,
    content_factory,
) -> None:
    content = content_factory(
        content_type=ContentType.ARTICLE,
        title="Pending Article",
        status=ContentStatus.PROCESSING,
        content_metadata={},
    )
    deck = LearningDeck(
        user_id=test_user.id,
        source_kind="content",
        source_identity=f"content:{content.id}",
        source_content_id=content.id,
        source_title=content.title,
        source_metadata={"content_type": "article"},
        title=content.title,
        artifact_object_keys=[],
    )
    db_session.add(deck)
    db_session.flush()
    run = LearningDeckRun(
        deck_id=deck.id,
        user_id=test_user.id,
        status="queued",
        source_snapshot={},
        timeline=[],
        artifact_object_keys=[],
    )
    db_session.add(run)
    db_session.commit()

    with pytest.raises(LearningDeckGenerationWaiting):
        generate_learning_deck(db_session, learning_deck_run_id=_required_id(run.id))

    db_session.refresh(run)
    assert run.status == "preparing"
    assert isinstance(run.timeline, list)
    assert run.timeline[-1]["note"] == "Source content is still processing"


def test_agent_failure_stores_internal_log_without_publishing_artifact(
    db_session,
    test_user,
    content_factory,
    tmp_path,
    monkeypatch,
) -> None:
    gateway = LocalObjectStorageGateway(tmp_path)
    monkeypatch.setattr(
        "app.services.learning_deck_artifacts.get_object_storage_gateway",
        lambda: gateway,
    )
    _deck, run = _create_run(db_session, test_user, content_factory)

    def fake_runner(_source_snapshot, _interests_prompt, _user_id, _run_id):
        raise LearningDeckAgentExecutionError(
            "agent crashed",
            agent_log_events=[
                {
                    "event_type": "bash",
                    "payload": {"command": "python build_deck.py", "stderr": "agent crashed"},
                }
            ],
            sandbox_provider="local",
            sandbox_id="sandbox-failure",
        )

    with pytest.raises(LearningDeckAgentExecutionError):
        generate_learning_deck(
            db_session,
            learning_deck_run_id=_required_id(run.id),
            agent_runner=fake_runner,
        )

    db_session.refresh(run)
    assert run.status == "failed"
    assert run.sandbox_provider == "local"
    assert run.sandbox_id == "sandbox-failure"
    assert run.deck_object_key is None
    assert run.agent_log_object_key
    assert b"python build_deck.py" in gateway.get_bytes(key=run.agent_log_object_key)
