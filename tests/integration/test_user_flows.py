"""High-signal end-to-end user flow tests."""

from __future__ import annotations

import json
from copy import deepcopy

from app.models.schema import ChatMessage, ContentStatusEntry, UserScraperConfig


def _build_completed_chat_payload(prompt: str, reply: str) -> str:
    """Build a serialized chat payload for completed async message polling."""
    return json.dumps(
        [
            {
                "parts": [
                    {
                        "content": prompt,
                        "timestamp": "2026-03-17T20:05:02.295881Z",
                        "part_kind": "user-prompt",
                    }
                ],
                "timestamp": "2026-03-17T20:05:02.296029Z",
                "instructions": None,
                "kind": "request",
                "run_id": "run-1",
                "metadata": None,
            },
            {
                "parts": [
                    {
                        "content": reply,
                        "id": None,
                        "provider_name": None,
                        "provider_details": None,
                        "part_kind": "text",
                    }
                ],
                "usage": {},
                "model_name": "gpt-5.4",
                "timestamp": "2026-03-17T20:05:04.689805Z",
                "kind": "response",
                "provider_name": "openai",
                "provider_url": "https://api.openai.com",
                "provider_details": None,
                "finish_reason": "stop",
                "run_id": "run-1",
                "metadata": None,
            },
        ]
    )


def test_onboarding_complete_seeds_configs_tasks_and_visible_content(
    client,
    content_factory,
    db_session,
    monkeypatch,
    sample_article_long,
    test_user,
):
    """Completing onboarding should create configs, queue work, and seed inbox content."""
    feed_url = "https://feeds.example.com/ai.atom"
    article_metadata = deepcopy(sample_article_long["content_metadata"])
    article_metadata["feed_url"] = feed_url

    seeded_article = content_factory(
        content_type="article",
        url="https://example.com/onboarding/article",
        title=sample_article_long["title"],
        source=sample_article_long["source"],
        status="completed",
        content_metadata=article_metadata,
    )
    seeded_news = content_factory(
        content_type="news",
        url="https://example.com/onboarding/news",
        title="Onboarding News Seed",
        status="completed",
        content_metadata={},
    )
    enqueued_tasks: list[tuple[str, dict | None]] = []

    class _FakeQueueGateway:
        def enqueue(self, task_type, payload=None, **_kwargs) -> int:
            enqueued_tasks.append((str(task_type), payload))
            return len(enqueued_tasks)

    monkeypatch.setattr(
        "app.models.internal.scraper_configs.FEED_VALIDATOR.validate_feed_url",
        lambda url: {"feed_url": url},
    )
    monkeypatch.setattr(
        "app.services.onboarding.get_task_queue_gateway",
        lambda: _FakeQueueGateway(),
    )

    response = client.post(
        "/api/onboarding/complete",
        json={
            "selected_sources": [
                {
                    "suggestion_type": "atom",
                    "title": "AI Feed",
                    "feed_url": feed_url,
                }
            ],
            "selected_subreddits": ["LocalLLaMA"],
            "profile_summary": "Follows AI product launches and engineering infra shifts.",
            "inferred_topics": ["AI", "infrastructure"],
            "twitter_username": "@willem_aw",
            "news_list_preference_prompt": "Prefer AI launches and engineering wins.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["has_completed_onboarding"] is True
    assert payload["inbox_count_estimate"] >= 100

    db_session.refresh(test_user)
    assert test_user.has_completed_onboarding is True
    assert test_user.twitter_username == "willem_aw"
    assert test_user.news_list_preference_prompt.startswith("Prefer AI launches")

    scraper_types = {
        row.scraper_type
        for row in db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == test_user.id)
        .all()
    }
    assert scraper_types == {"atom", "reddit"}

    queued_task_types = {task_type for task_type, _payload in enqueued_tasks}
    assert "scrape" in queued_task_types
    assert "onboarding_discover" in queued_task_types

    inbox_ids = {
        row.content_id
        for row in db_session.query(ContentStatusEntry)
        .filter(ContentStatusEntry.user_id == test_user.id)
        .all()
    }
    assert seeded_article.id in inbox_ids
    assert seeded_news.id in inbox_ids

    list_response = client.get("/api/content/")
    assert list_response.status_code == 200
    listed_ids = {item["id"] for item in list_response.json()["contents"]}
    assert seeded_article.id in listed_ids

    detail_response = client.get(f"/api/content/{seeded_article.id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["title"] == seeded_article.title


def test_list_detail_and_actions_flow_end_to_end(
    client,
    create_sample_content,
    sample_article_long,
):
    """A seeded inbox item should round-trip through list, detail, read, and knowledge save."""
    content = create_sample_content(sample_article_long)

    list_response = client.get("/api/content/")
    assert list_response.status_code == 200
    matching_items = [
        item for item in list_response.json()["contents"] if item["id"] == content.id
    ]
    assert len(matching_items) == 1
    assert matching_items[0]["is_read"] is False
    assert matching_items[0]["is_saved_to_knowledge"] is False

    detail_response = client.get(f"/api/content/{content.id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["title"] == content.title
    assert detail_response.json()["is_read"] is False
    assert detail_response.json()["is_saved_to_knowledge"] is False

    save_response = client.post(f"/api/content/{content.id}/knowledge")
    assert save_response.status_code == 200
    assert save_response.json()["is_saved_to_knowledge"] is True

    read_response = client.post(f"/api/content/{content.id}/mark-read")
    assert read_response.status_code == 200
    assert read_response.json()["status"] == "success"

    refreshed_detail = client.get(f"/api/content/{content.id}")
    assert refreshed_detail.status_code == 200
    assert refreshed_detail.json()["is_read"] is True
    assert refreshed_detail.json()["is_saved_to_knowledge"] is True

    refreshed_list = client.get("/api/content/")
    assert refreshed_list.status_code == 200
    refreshed_item = next(
        item for item in refreshed_list.json()["contents"] if item["id"] == content.id
    )
    assert refreshed_item["is_read"] is True
    assert refreshed_item["is_saved_to_knowledge"] is True


def test_chat_session_message_and_status_flow_end_to_end(
    client,
    create_sample_content,
    db_session,
    monkeypatch,
    sample_article_long,
):
    """Chat flow should create a session, process a message, and expose it in status/detail APIs."""
    content = create_sample_content(sample_article_long)

    async def _fake_process_message_async(
        session_id: int,
        message_id: int,
        prompt: str,
        source: str = "chat",
        screen_context=None,
    ) -> None:
        del session_id, screen_context, source
        db_message = db_session.query(ChatMessage).filter(ChatMessage.id == message_id).one()
        db_message.message_list = _build_completed_chat_payload(
            prompt,
            "The most important point is the operational constraint behind the rollout.",
        )
        db_message.status = "completed"
        db_session.commit()

    monkeypatch.setattr("app.routers.api.chat.process_message_async", _fake_process_message_async)
    monkeypatch.setattr(
        "app.routers.api.chat.process_assistant_turn_async",
        _fake_process_message_async,
    )

    create_response = client.post(
        "/api/content/chat/sessions",
        json={
            "content_id": content.id,
            "topic": "Operational implications",
        },
    )
    assert create_response.status_code == 200
    session = create_response.json()["session"]
    session_id = session["id"]
    assert session["content_id"] == content.id

    send_response = client.post(
        f"/api/content/chat/sessions/{session_id}/messages",
        json={"message": "What matters most here?"},
    )
    assert send_response.status_code == 200
    send_payload = send_response.json()
    assert send_payload["status"] == "processing"

    status_response = client.get(
        f"/api/content/chat/messages/{send_payload['message_id']}/status"
    )
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["status"] == "completed"
    assert (
        "operational constraint"
        in status_payload["assistant_message"]["content"].casefold()
    )

    detail_response = client.get(f"/api/content/chat/sessions/{session_id}")
    assert detail_response.status_code == 200
    roles = [message["role"] for message in detail_response.json()["messages"]]
    assert roles.count("user") == 1
    assert roles.count("assistant") == 1

    list_response = client.get("/api/content/chat/sessions")
    assert list_response.status_code == 200
    sessions = list_response.json()
    assert any(entry["id"] == session_id for entry in sessions)
