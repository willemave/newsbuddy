from __future__ import annotations

from types import SimpleNamespace

from app.models.contracts import ContentStatus, ContentType, TaskType
from app.models.db import (
    Content,
    ContentKnowledgeSave,
    ContentReadStatus,
    LearningDeck,
    LlmTask,
    ProcessingTask,
)
from app.services.gateways.object_storage_gateway import LocalObjectStorageGateway
from tests.support.builders import create_content_status_entry_row


def _create_visible_article(db_session, test_user, content_factory):
    content = content_factory(
        content_type=ContentType.ARTICLE,
        title="Learning Deck API Source",
        content_metadata={"content": "API source body for a learning deck."},
    )
    create_content_status_entry_row(db_session, user=test_user, content=content)
    return content


def test_create_learning_deck_endpoint_enqueues_generation(
    client,
    db_session,
    test_user,
    content_factory,
) -> None:
    content = _create_visible_article(db_session, test_user, content_factory)

    response = client.post(
        "/api/learning/decks",
        json={"content_id": content.id, "interests_prompt": "Teach the architecture"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["source_content_id"] == content.id
    assert payload["status"] == "queued"
    assert payload["latest_run"]["interests_prompt"] == "Teach the architecture"
    task = db_session.query(ProcessingTask).one()
    assert task.task_type == TaskType.RUN_LLM_TASK.value
    assert task.queue_name == "llm"


def test_retry_learning_deck_endpoint_starts_one_new_attempt(
    client,
    db_session,
    test_user,
    content_factory,
) -> None:
    content = _create_visible_article(db_session, test_user, content_factory)
    create_response = client.post("/api/learning/decks", json={"content_id": content.id})
    deck_id = create_response.json()["id"]
    deck = db_session.query(LearningDeck).filter_by(id=deck_id).one()
    failed_task = db_session.query(LlmTask).filter_by(id=deck.latest_task_id).one()
    failed_task.status = "failed"
    failed_task.workflow_state = "failed"
    db_session.commit()

    response = client.post(f"/api/learning/decks/{deck_id}/retry")

    assert response.status_code == 202
    payload = response.json()
    assert payload["id"] == deck_id
    assert payload["status"] == "queued"
    retry_task_id = payload["latest_run"]["id"]
    assert retry_task_id != failed_task.id

    repeated = client.post(f"/api/learning/decks/{deck_id}/retry")

    assert repeated.status_code == 202
    assert repeated.json()["latest_run"]["id"] == retry_task_id
    assert db_session.query(LlmTask).filter_by(subject_id=deck_id).count() == 2


def test_retry_learning_deck_endpoint_requires_owned_failed_deck(
    client,
    db_session,
    test_user,
    content_factory,
) -> None:
    content = _create_visible_article(db_session, test_user, content_factory)
    create_response = client.post("/api/learning/decks", json={"content_id": content.id})
    deck_id = create_response.json()["id"]
    deck = db_session.query(LearningDeck).filter_by(id=deck_id).one()
    task = db_session.query(LlmTask).filter_by(id=deck.latest_task_id).one()
    task.status = "completed"
    db_session.commit()

    assert client.post(f"/api/learning/decks/{deck_id}/retry").status_code == 409
    assert client.post("/api/learning/decks/999999/retry").status_code == 404


def test_create_learning_deck_from_url_saves_to_knowledge_and_skips_unread_long_read(
    client,
    db_session,
    test_user,
) -> None:
    response = client.post(
        "/api/learning/decks",
        json={"url": "https://example.com/deck-source"},
    )

    assert response.status_code == 202
    payload = response.json()
    source_content_id = payload["source_content_id"]
    assert source_content_id is not None

    source_content = db_session.query(Content).filter_by(id=source_content_id).one()
    assert (
        db_session.query(ContentKnowledgeSave)
        .filter_by(user_id=test_user.id, content_id=source_content_id)
        .one_or_none()
        is not None
    )
    assert (
        db_session.query(ContentReadStatus)
        .filter_by(user_id=test_user.id, content_id=source_content_id)
        .one_or_none()
        is not None
    )

    source_content.content_type = ContentType.ARTICLE.value
    source_content.status = ContentStatus.COMPLETED.value
    db_session.commit()

    unread_long_read = client.get(
        "/api/content/",
        params={"content_type": ContentType.ARTICLE.value, "read_filter": "unread"},
    )

    assert unread_long_read.status_code == 200
    unread_ids = {item["id"] for item in unread_long_read.json()["contents"]}
    assert source_content_id not in unread_ids


def test_private_viewer_url_requires_completed_deck(
    client,
    db_session,
    test_user,
    content_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.core.external_urls.get_settings",
        lambda: SimpleNamespace(public_base_url="https://public.example.com"),
    )
    content = _create_visible_article(db_session, test_user, content_factory)
    create_response = client.post("/api/learning/decks", json={"content_id": content.id})
    deck_id = create_response.json()["id"]

    not_ready = client.post(f"/api/learning/decks/{deck_id}/viewer-url")
    assert not_ready.status_code == 409

    deck = db_session.query(LearningDeck).filter_by(id=deck_id).one()
    task = db_session.query(LlmTask).filter_by(id=deck.latest_task_id).one()
    task.status = "completed"
    deck.latest_successful_task_id = task.id
    deck.deck_object_key = "learning/test/index.html"
    db_session.commit()

    ready = client.post(f"/api/learning/decks/{deck_id}/viewer-url")
    assert ready.status_code == 200
    assert ready.json()["url"].startswith("https://public.example.com/learning/signed/")
    assert ready.json()["expires_at"]

    source_notes = client.post(f"/api/learning/decks/{deck_id}/source-notes-url")
    assert source_notes.status_code == 200
    assert source_notes.json()["url"].startswith("https://public.example.com/learning/signed/")

    share = client.post(f"/api/learning/decks/{deck_id}/share")
    assert share.status_code == 200
    assert share.json()["share_url"].startswith("https://public.example.com/learning/share/")


def test_public_share_route_serves_latest_artifact_and_disable_revokes(
    client,
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
    content = _create_visible_article(db_session, test_user, content_factory)
    create_response = client.post("/api/learning/decks", json={"content_id": content.id})
    deck_id = create_response.json()["id"]
    deck = db_session.query(LearningDeck).filter_by(id=deck_id).one()
    task = db_session.query(LlmTask).filter_by(id=deck.latest_task_id).one()
    gateway.put_text(
        key="learning/deck/index.html",
        text="<html><body>Shared Learning Deck</body></html>",
        content_type="text/html",
    )
    gateway.put_text(
        key="learning/deck/source-notes.html",
        text="<html><body>Shared Source Notes</body></html>",
        content_type="text/html",
    )
    gateway.put_text(
        key="learning/deck/assets/theme.css",
        text=".reveal { color: rgb(20 20 20); }",
        content_type="text/css",
    )
    task.status = "completed"
    deck.latest_successful_task_id = task.id
    deck.artifact_storage_prefix = "learning/deck"
    deck.deck_object_key = "learning/deck/index.html"
    deck.source_notes_html_object_key = "learning/deck/source-notes.html"
    deck.artifact_object_keys = [
        "learning/deck/index.html",
        "learning/deck/source-notes.html",
        "learning/deck/assets/theme.css",
    ]
    db_session.commit()

    share_response = client.post(f"/api/learning/decks/{deck_id}/share")
    assert share_response.status_code == 200
    share_url = share_response.json()["share_url"]
    assert share_url is not None

    shared = client.get(share_url)
    assert shared.status_code == 200
    assert "Shared Learning Deck" in shared.text
    assert 'data-newsly-learning-deck-controls="style"' in shared.text
    assert 'data-newsly-learning-deck-controls="script"' in shared.text
    assert 'data-newsly-learning-deck-controls="controls"' not in shared.text
    assert "data-newsly-learning-deck-fullscreen" not in shared.text
    assert "data-newsly-learning-deck-next" not in shared.text
    assert "data-newsly-learning-deck-prev" not in shared.text
    notes = client.get(f"{share_url.rstrip('/')}/source-notes")
    assert notes.status_code == 200
    assert "Shared Source Notes" in notes.text
    assert "data-newsly-learning-deck-controls" not in notes.text
    shared_asset = client.get(f"{share_url.rstrip('/')}/assets/theme.css")
    assert shared_asset.status_code == 200
    assert "rgb(20 20 20)" in shared_asset.text

    private_url = client.post(f"/api/learning/decks/{deck_id}/viewer-url").json()["url"]
    private = client.get(private_url)
    assert private.status_code == 200
    assert "data-newsly-learning-deck-fullscreen" not in private.text
    assert "data-newsly-learning-deck-next" not in private.text
    assert "data-newsly-learning-deck-prev" not in private.text
    private_asset = client.get(f"{private_url.rstrip('/')}/assets/theme.css")
    assert private_asset.status_code == 200
    assert "rgb(20 20 20)" in private_asset.text
    assert client.get(f"{private_url.rstrip('/')}/assets/missing.css").status_code == 404

    disable_response = client.delete(f"/api/learning/decks/{deck_id}/share")
    assert disable_response.status_code == 200
    assert disable_response.json() == {"share_enabled": False, "share_url": None}
    assert client.get(share_url).status_code == 404
    assert client.get(f"{share_url.rstrip('/')}/assets/theme.css").status_code == 404
