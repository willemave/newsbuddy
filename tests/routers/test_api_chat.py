"""Tests for chat session API endpoints."""

import asyncio
import json

from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from sqlalchemy.orm import Session

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import ChatMessage, ChatSession, Content
from app.services.chat_agent import ChatRunResult, save_messages


def test_create_chat_session_with_content(
    client: TestClient, db_session: Session, test_user
) -> None:
    """Test creating a chat session associated with content."""
    # Create test content
    content = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Test Article About AI",
        source="Test Source",
        content_metadata={
            "summary": {
                "title": "Test Article",
                "overview": "This is a test article overview that is long enough for validation.",
                "bullet_points": [
                    {"text": "Key point 1", "category": "key_finding"},
                    {"text": "Key point 2", "category": "methodology"},
                    {"text": "Key point 3", "category": "conclusion"},
                ],
                "quotes": [],
                "topics": ["AI", "Technology"],
            },
            "summary_kind": "long_structured",
            "summary_version": 1,
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    # Create chat session
    response = client.post(
        "/api/content/chat/sessions",
        json={
            "content_id": content.id,
            "llm_provider": "openai",
        },
    )
    assert response.status_code == 200

    data = response.json()
    assert "session" in data
    session = data["session"]
    assert session["content_id"] == content.id
    assert session["llm_provider"] == "openai"
    assert session["llm_model"] == "openai:gpt-5.4"
    assert session["session_type"] == "knowledge_chat"
    assert session["article_title"] == "Test Article About AI"
    assert session["article_summary"] is not None
    assert session["article_source"] == "Test Source"

    # Verify session in database
    db_session_record = (
        db_session.query(ChatSession).filter(ChatSession.id == session["id"]).first()
    )
    assert db_session_record is not None
    assert db_session_record.user_id == test_user.id
    assert db_session_record.context_snapshot is not None
    assert "Short Summary:" in db_session_record.context_snapshot


def test_create_chat_session_with_topic(
    client: TestClient, db_session: Session, test_user
) -> None:
    """Test creating a chat session with a specific topic."""
    # Create test content
    content = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Test Article",
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    response = client.post(
        "/api/content/chat/sessions",
        json={
            "content_id": content.id,
            "topic": "AI safety implications",
        },
    )
    assert response.status_code == 200

    data = response.json()
    session = data["session"]
    assert session["topic"] == "AI safety implications"
    assert session["session_type"] == "knowledge_chat"
    assert "AI safety implications" in session["title"]


def test_create_chat_session_without_content(
    client: TestClient, db_session: Session
) -> None:
    """Test creating an ad-hoc chat session without content."""
    response = client.post(
        "/api/content/chat/sessions",
        json={
            "initial_message": "What is the meaning of life?",
        },
    )
    assert response.status_code == 200

    data = response.json()
    session = data["session"]
    assert session["content_id"] is None
    assert session["session_type"] == "knowledge_chat"
    assert session["title"] == "What is the meaning of life?"


def test_create_chat_session_content_not_found(client: TestClient) -> None:
    """Test creating chat session with non-existent content."""
    response = client.post(
        "/api/content/chat/sessions",
        json={"content_id": 99999},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_list_chat_sessions(
    client: TestClient, db_session: Session, test_user
) -> None:
    """Test listing chat sessions for current user."""
    # Create test content
    content = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Test Article",
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    # Create multiple sessions
    for i in range(3):
        session = ChatSession(
            user_id=test_user.id,
            content_id=content.id if i == 0 else None,
            title=f"Session {i}",
            session_type="knowledge_chat",
            llm_model="openai:gpt-5.4",
            llm_provider="openai",
        )
        db_session.add(session)
    db_session.commit()

    # List sessions
    response = client.get("/api/content/chat/sessions")
    assert response.status_code == 200

    sessions = response.json()
    assert len(sessions) == 3


def _seed_turn(
    db_session: Session,
    session_id: int,
    user_prompt: str,
    assistant_text: str,
) -> ChatMessage:
    return save_messages(
        db_session,
        session_id,
        [
            ModelRequest(parts=[UserPromptPart(content=user_prompt)]),
            ModelResponse(parts=[TextPart(content=assistant_text)]),
        ],
        display_user_prompt=user_prompt,
    )


def test_start_council_chat_creates_hidden_child_sessions_and_hides_them_from_history(
    client: TestClient,
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Council start should fork hidden branches and expose only the parent session."""
    parent = ChatSession(
        user_id=test_user.id,
        title="Council Parent",
        session_type="knowledge_chat",
        context_snapshot="Parent context",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add(parent)
    db_session.commit()
    db_session.refresh(parent)
    _seed_turn(db_session, parent.id, "What happened?", "Initial answer.")

    async def _fake_run_chat_turn(db, session, user_prompt, source="chat"):
        del source
        assistant_text = f"{session.council_persona_name} branch on: {user_prompt}"
        messages = [
            ModelRequest(parts=[UserPromptPart(content=user_prompt)]),
            ModelResponse(parts=[TextPart(content=assistant_text)]),
        ]
        save_messages(db, session.id, messages, display_user_prompt=user_prompt)
        return ChatRunResult(
            output_text=assistant_text,
            new_messages=messages,
            all_messages=messages,
            tool_calls=[],
        )

    monkeypatch.setattr("app.services.council_chat.run_chat_turn", _fake_run_chat_turn)

    response = client.post(
        f"/api/content/chat/sessions/{parent.id}/council/start",
        json={"message": "Give me four different perspectives."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["id"] == parent.id
    assert payload["session"]["council_mode"] is True
    assert payload["session"]["active_child_session_id"] is not None

    db_session.refresh(parent)
    assert parent.council_mode is True
    assert parent.active_child_session_id is not None
    assert parent.council_message_id is not None

    db_session.expire_all()
    child_sessions = (
        db_session.query(ChatSession)
        .filter(ChatSession.parent_session_id == parent.id)
        .order_by(ChatSession.id)
        .all()
    )
    assert len(child_sessions) == 4
    assert all(child.is_hidden_from_history is True for child in child_sessions)
    assert all(child.branch_start_message_id is not None for child in child_sessions)
    assert all(
        "Council Response Style:" in (child.context_snapshot or "")
        for child in child_sessions
    )
    assert all(
        "Keep responses concise by default." in (child.context_snapshot or "")
        for child in child_sessions
    )

    council_rows = [
        message
        for message in payload["messages"]
        if message["role"] == "assistant" and message["council_candidates"]
    ]
    assert len(council_rows) == 1
    assert len(council_rows[0]["council_candidates"]) == 4

    history_response = client.get("/api/content/chat/sessions")
    assert history_response.status_code == 200
    history_payload = history_response.json()
    assert [session["id"] for session in history_payload] == [parent.id]


def test_council_branch_selection_switches_visible_transcript_and_send_targets_active_child(
    client: TestClient,
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Selecting a council branch should swap downstream transcript and follow-up routing."""
    parent = ChatSession(
        user_id=test_user.id,
        title="Switchable Council",
        session_type="knowledge_chat",
        context_snapshot="Parent context",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add(parent)
    db_session.commit()
    db_session.refresh(parent)
    _seed_turn(db_session, parent.id, "Summarize this.", "Base answer.")

    async def _fake_run_chat_turn(db, session, user_prompt, source="chat"):
        del source
        assistant_text = f"{session.council_persona_name} initial council reply"
        messages = [
            ModelRequest(parts=[UserPromptPart(content=user_prompt)]),
            ModelResponse(parts=[TextPart(content=assistant_text)]),
        ]
        save_messages(db, session.id, messages, display_user_prompt=user_prompt)
        return ChatRunResult(
            output_text=assistant_text,
            new_messages=messages,
            all_messages=messages,
            tool_calls=[],
        )

    monkeypatch.setattr("app.services.council_chat.run_chat_turn", _fake_run_chat_turn)

    start_response = client.post(
        f"/api/content/chat/sessions/{parent.id}/council/start",
        json={"message": "Open a council."},
    )
    assert start_response.status_code == 200
    start_payload = start_response.json()
    council_row = next(
        message
        for message in start_payload["messages"]
        if message["role"] == "assistant" and message["council_candidates"]
    )
    candidates = council_row["council_candidates"]
    first_child_id = candidates[0]["child_session_id"]
    second_child_id = candidates[1]["child_session_id"]

    _seed_turn(db_session, first_child_id, "First branch follow-up", "First branch answer")
    _seed_turn(db_session, second_child_id, "Second branch follow-up", "Second branch answer")

    routed_calls: list[tuple[int, int, str]] = []

    async def _fake_process_assistant_turn_async(
        session_id: int,
        message_id: int,
        prompt: str,
        *,
        screen_context,
        source: str = "assistant",
    ) -> None:
        del screen_context, source
        routed_calls.append((session_id, message_id, prompt))

    monkeypatch.setattr(
        "app.routers.api.chat.process_assistant_turn_async",
        _fake_process_assistant_turn_async,
    )

    send_response = client.post(
        f"/api/content/chat/sessions/{parent.id}/messages",
        json={"message": "Stay on the default branch."},
    )
    assert send_response.status_code == 200
    assert routed_calls[0][0] == first_child_id

    second_select_response = client.post(
        f"/api/content/chat/sessions/{parent.id}/council/select",
        json={"child_session_id": second_child_id},
    )
    assert second_select_response.status_code == 200
    second_payload = second_select_response.json()
    assert second_payload["session"]["active_child_session_id"] == second_child_id
    visible_texts = [message["content"] for message in second_payload["messages"]]
    assert "Second branch answer" in visible_texts
    assert "First branch answer" not in visible_texts

    send_after_switch_response = client.post(
        f"/api/content/chat/sessions/{parent.id}/messages",
        json={"message": "Route this to the second branch."},
    )
    assert send_after_switch_response.status_code == 200
    assert routed_calls[1][0] == second_child_id

    first_select_response = client.post(
        f"/api/content/chat/sessions/{parent.id}/council/select",
        json={"child_session_id": first_child_id},
    )
    assert first_select_response.status_code == 200
    first_payload = first_select_response.json()
    assert first_payload["session"]["active_child_session_id"] == first_child_id
    visible_texts = [message["content"] for message in first_payload["messages"]]
    assert "First branch answer" in visible_texts
    assert "Second branch answer" not in visible_texts


def test_start_council_chat_runs_branches_in_parallel(
    client: TestClient,
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Council start should launch all four branch turns before awaiting completion."""
    parent = ChatSession(
        user_id=test_user.id,
        title="Parallel Council",
        session_type="knowledge_chat",
        context_snapshot="Parent context",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add(parent)
    db_session.commit()
    db_session.refresh(parent)

    release_all = asyncio.Event()
    branch_started = 0

    async def _fake_run_chat_turn(db, session, user_prompt, source="chat"):
        del source
        nonlocal branch_started
        branch_started += 1
        if branch_started == 4:
            release_all.set()
        await asyncio.wait_for(release_all.wait(), timeout=0.2)
        assistant_text = f"{session.council_persona_name} parallel reply"
        messages = [
            ModelRequest(parts=[UserPromptPart(content=user_prompt)]),
            ModelResponse(parts=[TextPart(content=assistant_text)]),
        ]
        save_messages(db, session.id, messages, display_user_prompt=user_prompt)
        return ChatRunResult(
            output_text=assistant_text,
            new_messages=messages,
            all_messages=messages,
            tool_calls=[],
        )

    monkeypatch.setattr("app.services.council_chat.run_chat_turn", _fake_run_chat_turn)

    response = client.post(
        f"/api/content/chat/sessions/{parent.id}/council/start",
        json={"message": "Compare the tradeoffs."},
    )

    assert response.status_code == 200
    assert branch_started == 4


def test_delete_chat_session_archives_council_children(
    client: TestClient,
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Deleting a council parent should archive all hidden child branches."""
    parent = ChatSession(
        user_id=test_user.id,
        title="Council Delete",
        session_type="knowledge_chat",
        context_snapshot="Parent context",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add(parent)
    db_session.commit()
    db_session.refresh(parent)
    _seed_turn(db_session, parent.id, "Question", "Answer")

    async def _fake_run_chat_turn(db, session, user_prompt, source="chat"):
        del source
        assistant_text = f"{session.council_persona_name} says {user_prompt}"
        messages = [
            ModelRequest(parts=[UserPromptPart(content=user_prompt)]),
            ModelResponse(parts=[TextPart(content=assistant_text)]),
        ]
        save_messages(db, session.id, messages, display_user_prompt=user_prompt)
        return ChatRunResult(
            output_text=assistant_text,
            new_messages=messages,
            all_messages=messages,
            tool_calls=[],
        )

    monkeypatch.setattr("app.services.council_chat.run_chat_turn", _fake_run_chat_turn)

    start_response = client.post(
        f"/api/content/chat/sessions/{parent.id}/council/start",
        json={"message": "Start council."},
    )
    assert start_response.status_code == 200

    delete_response = client.delete(f"/api/content/chat/sessions/{parent.id}")
    assert delete_response.status_code == 204

    db_session.refresh(parent)
    assert parent.is_archived is True

    child_sessions = (
        db_session.query(ChatSession)
        .filter(ChatSession.parent_session_id == parent.id)
        .order_by(ChatSession.id)
        .all()
    )
    assert len(child_sessions) == 4
    assert all(child.is_archived is True for child in child_sessions)


def test_list_chat_sessions_filter_by_content(
    client: TestClient, db_session: Session, test_user
) -> None:
    """Test listing chat sessions filtered by content ID."""
    # Create test content
    content1 = Content(
        url="https://example.com/article1",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Article 1",
    )
    content2 = Content(
        url="https://example.com/article2",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Article 2",
    )
    db_session.add_all([content1, content2])
    db_session.commit()
    db_session.refresh(content1)
    db_session.refresh(content2)

    # Create sessions for different content
    session1 = ChatSession(
        user_id=test_user.id,
        content_id=content1.id,
        title="Session for Article 1",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    session2 = ChatSession(
        user_id=test_user.id,
        content_id=content2.id,
        title="Session for Article 2",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add_all([session1, session2])
    db_session.commit()

    # Filter by content_id
    response = client.get(f"/api/content/chat/sessions?content_id={content1.id}")
    assert response.status_code == 200

    sessions = response.json()
    assert len(sessions) == 1
    assert sessions[0]["content_id"] == content1.id


def test_get_chat_session_detail(
    client: TestClient, db_session: Session, test_user
) -> None:
    """Test getting chat session details."""
    session = ChatSession(
        user_id=test_user.id,
        title="Test Session",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    response = client.get(f"/api/content/chat/sessions/{session.id}")
    assert response.status_code == 200

    data = response.json()
    assert "session" in data
    assert "messages" in data
    assert data["session"]["id"] == session.id
    assert data["messages"] == []  # No messages yet


def test_get_chat_session_detail_includes_assistant_feed_options(
    client: TestClient,
    db_session: Session,
    test_user,
) -> None:
    """Assistant messages should expose structured feed options in session detail."""

    session = ChatSession(
        user_id=test_user.id,
        title="Quick Assistant",
        session_type="assistant_quick",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    payload = json.dumps(
        [
            {
                "parts": [
                    {
                        "content": "Find me Armin Ronacher's blog.",
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
                        "content": "I found a validated feed option below.",
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
    db_session.add(
        ChatMessage(
            session_id=session.id,
            message_list=payload,
            render_metadata={
                "feed_options": [
                    {
                        "id": "8f7d2c42b0c1de90",
                        "title": "lucumr",
                        "site_url": "https://lucumr.pocoo.org/",
                        "feed_url": "https://lucumr.pocoo.org/feed.atom",
                        "feed_type": "atom",
                        "feed_format": "atom",
                        "description": "Armin Ronacher's weblog.",
                        "rationale": "Validated Atom feed.",
                        "evidence_url": "https://lucumr.pocoo.org/",
                    }
                ]
            },
            status="completed",
        )
    )
    db_session.commit()

    response = client.get(f"/api/content/chat/sessions/{session.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["messages"][-1]["role"] == "assistant"
    assert data["messages"][-1]["feed_options"][0]["feed_url"] == "https://lucumr.pocoo.org/feed.atom"


def test_get_chat_session_detail_exposes_source_message_id_for_pending_rows(
    client: TestClient,
    db_session: Session,
    test_user,
) -> None:
    """Pending session detail rows should expose their backing async message ID."""

    session = ChatSession(
        user_id=test_user.id,
        title="Pending Session",
        session_type="assistant_quick",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    payload = json.dumps(
        [
            {
                "parts": [
                    {
                        "content": "Give me a summary of the last day.",
                        "timestamp": "2026-03-23T20:41:07.295881Z",
                        "part_kind": "user-prompt",
                    }
                ],
                "timestamp": "2026-03-23T20:41:07.296029Z",
                "instructions": None,
                "kind": "request",
                "run_id": "run-pending",
                "metadata": None,
            }
        ]
    )
    db_message = ChatMessage(
        session_id=session.id,
        message_list=payload,
        status="processing",
    )
    db_session.add(db_message)
    db_session.commit()
    db_session.refresh(db_message)

    response = client.get(f"/api/content/chat/sessions/{session.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["messages"][0]["status"] == "processing"
    assert data["messages"][0]["source_message_id"] == db_message.id


def test_get_chat_session_not_found(client: TestClient) -> None:
    """Test getting non-existent chat session."""
    response = client.get("/api/content/chat/sessions/99999")
    assert response.status_code == 404


def test_get_chat_session_wrong_user(
    client: TestClient, db_session: Session
) -> None:
    """Test that users cannot access other users' sessions."""
    # Create session for a different user
    session = ChatSession(
        user_id=99999,  # Different user
        title="Other User's Session",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    response = client.get(f"/api/content/chat/sessions/{session.id}")
    assert response.status_code == 403


def test_delete_chat_session_archives_session(
    client: TestClient, db_session: Session, test_user
) -> None:
    """Test deleting a chat session archives it and hides it from list endpoint."""
    session = ChatSession(
        user_id=test_user.id,
        title="Session to delete",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    response = client.delete(f"/api/content/chat/sessions/{session.id}")
    assert response.status_code == 204

    db_session.refresh(session)
    assert session.is_archived is True

    list_response = client.get("/api/content/chat/sessions")
    assert list_response.status_code == 200
    assert session.id not in {item["id"] for item in list_response.json()}


def test_delete_chat_session_not_found(client: TestClient) -> None:
    """Test deleting a non-existent chat session."""
    response = client.delete("/api/content/chat/sessions/99999")
    assert response.status_code == 404


def test_delete_chat_session_wrong_user(client: TestClient, db_session: Session) -> None:
    """Test that users cannot delete other users' sessions."""
    session = ChatSession(
        user_id=99999,  # Different user
        title="Other User Session",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    response = client.delete(f"/api/content/chat/sessions/{session.id}")
    assert response.status_code == 403

    db_session.refresh(session)
    assert session.is_archived is False


def test_different_llm_providers(
    client: TestClient, db_session: Session
) -> None:
    """Test creating sessions with different LLM providers."""
    providers = [
        ("openai", "openai:gpt-5.4"),
        ("anthropic", "anthropic:claude-opus-4-5-20251101"),
        ("google", "google-gla:gemini-3-pro-preview"),
    ]

    for provider, expected_model in providers:
        response = client.post(
            "/api/content/chat/sessions",
            json={"llm_provider": provider},
        )
        assert response.status_code == 200

        data = response.json()
        session = data["session"]
        assert session["llm_provider"] == provider
        assert session["llm_model"] == expected_model


def test_create_assistant_turn_creates_session_with_screen_context(
    client: TestClient,
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Test creating a contextual assistant turn seeds a knowledge chat session."""
    content = Content(
        url="https://example.com/ai-news",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="AI Infrastructure Update",
        source="Example",
        content_metadata={
            "summary": {
                "title": "AI Infrastructure Update",
                "overview": (
                    "A grounded summary of recent AI infrastructure moves across chips, "
                    "cloud capacity, and developer tooling."
                ),
                "bullet_points": [
                    {"text": "Cloud providers are expanding AI capacity.", "category": "context"},
                    {"text": "Inference demand is increasing.", "category": "key_finding"},
                ],
                "quotes": [],
                "topics": ["AI infrastructure"],
            }
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    captured: list[tuple[int, int, str, str]] = []

    async def _fake_process_assistant_turn_async(
        session_id: int,
        message_id: int,
        prompt: str,
        *,
        screen_context,
        source: str = "assistant",
    ) -> None:
        captured.append((session_id, message_id, prompt, screen_context.screen_type))

    monkeypatch.setattr(
        "app.routers.api.chat.process_assistant_turn_async",
        _fake_process_assistant_turn_async,
    )
    response = client.post(
        "/api/content/chat/assistant/turns",
        json={
            "message": "Find me more coverage like this.",
            "screen_context": {
                "screen_type": "content_detail",
                "screen_title": "Article Detail",
                "content_id": content.id,
                "visible_content_ids": [content.id],
                "selected_topic": "ai",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "processing"
    assert payload["session"]["session_type"] == "knowledge_chat"
    assert payload["session"]["content_id"] == content.id
    assert payload["session"]["title"] == "AI Infrastructure Update"
    assert payload["user_message"]["content"] == "Find me more coverage like this."
    assert captured == [
        (
            payload["session"]["id"],
            payload["message_id"],
            "Find me more coverage like this.",
            "content_detail",
        )
    ]

    session = (
        db_session.query(ChatSession).filter(ChatSession.id == payload["session"]["id"]).first()
    )
    assert session is not None
    assert session.session_type == "knowledge_chat"
    assert session.context_snapshot is not None
    assert "Screen Type: content_detail" in session.context_snapshot
    assert f"[{content.id}] AI Infrastructure Update" in session.context_snapshot
    assert (
        "Short Summary:" in session.context_snapshot
        or "Transcript Excerpt:" in session.context_snapshot
    )


def test_create_assistant_turn_titles_new_ad_hoc_session_from_message(
    client: TestClient,
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Ad-hoc assistant turns should use the first message as the session title."""
    captured: list[tuple[int, int, str, str]] = []

    async def _fake_process_assistant_turn_async(
        session_id: int,
        message_id: int,
        prompt: str,
        *,
        screen_context,
        source: str = "assistant",
    ) -> None:
        del source
        captured.append((session_id, message_id, prompt, screen_context.screen_type))

    monkeypatch.setattr(
        "app.routers.api.chat.process_assistant_turn_async",
        _fake_process_assistant_turn_async,
    )
    message = "Recommend a few feeds and newsletters I should add based on what I've read lately."
    response = client.post(
        "/api/content/chat/assistant/turns",
        json={
            "message": message,
            "screen_context": {
                "screen_type": "knowledge_hub",
                "screen_title": "Knowledge",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "processing"
    assert payload["session"]["content_id"] is None
    assert payload["session"]["title"] == message[:80]
    assert payload["user_message"]["content"] == message
    assert captured == [
        (
            payload["session"]["id"],
            payload["message_id"],
            message,
            "knowledge_hub",
        )
    ]

    session = (
        db_session.query(ChatSession).filter(ChatSession.id == payload["session"]["id"]).first()
    )
    assert session is not None
    assert session.title == message[:80]
    assert session.context_snapshot is not None
    assert "Screen Type: knowledge_hub" in session.context_snapshot


def test_create_assistant_turn_truncates_visible_content_ids(
    client: TestClient,
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Test oversized visible-content context is truncated instead of rejected."""
    captured: list[list[int]] = []

    async def _fake_process_assistant_turn_async(
        session_id: int,
        message_id: int,
        prompt: str,
        *,
        screen_context,
        source: str = "assistant",
    ) -> None:
        del session_id, message_id, prompt, source
        captured.append(screen_context.visible_content_ids)

    monkeypatch.setattr(
        "app.routers.api.chat.process_assistant_turn_async",
        _fake_process_assistant_turn_async,
    )
    response = client.post(
        "/api/content/chat/assistant/turns",
        json={
            "message": "What's the weather tomorrow?",
            "screen_context": {
                "screen_type": "long_form",
                "screen_title": "Long Form",
                "visible_content_ids": list(range(1, 20)),
            },
        },
    )

    assert response.status_code == 200
    assert captured == [list(range(1, 13))]


def test_create_assistant_turn_refreshes_existing_session_context(
    client: TestClient,
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Continuing an assistant session should use the latest screen context."""

    old_content = Content(
        url="https://example.com/old-context",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Old Context",
    )
    new_content = Content(
        url="https://example.com/new-context",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="New Context",
    )
    db_session.add_all([old_content, new_content])
    db_session.commit()
    db_session.refresh(old_content)
    db_session.refresh(new_content)

    session = ChatSession(
        user_id=test_user.id,
        content_id=old_content.id,
        title="Old Context",
        session_type="knowledge_chat",
        context_snapshot="Screen Type: content_detail\nVisible Content:\n- [1] Old Context",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    captured: list[tuple[int, str]] = []

    async def _fake_process_assistant_turn_async(
        session_id: int,
        message_id: int,
        prompt: str,
        *,
        screen_context,
        source: str = "assistant",
    ) -> None:
        del message_id, prompt, source
        captured.append((session_id, screen_context.screen_type))

    monkeypatch.setattr(
        "app.routers.api.chat.process_assistant_turn_async",
        _fake_process_assistant_turn_async,
    )
    response = client.post(
        "/api/content/chat/assistant/turns",
        json={
            "session_id": session.id,
            "message": "Use what I'm looking at now.",
            "screen_context": {
                "screen_type": "content_detail",
                "screen_title": "Article Detail",
                "content_id": new_content.id,
                "visible_content_ids": [new_content.id],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["id"] == session.id
    assert payload["session"]["content_id"] == new_content.id
    assert payload["session"]["title"] == "New Context"
    assert captured == [(session.id, "content_detail")]

    db_session.refresh(session)
    assert session.content_id == new_content.id
    assert session.title == "New Context"
    assert session.context_snapshot is not None
    assert f"[{new_content.id}] New Context" in session.context_snapshot


def test_send_message_routes_assistant_sessions_to_assistant_processor(
    client: TestClient,
    db_session: Session,
    test_user,
    monkeypatch,
) -> None:
    """Test assistant sessions keep using the assistant router on follow-up turns."""
    session = ChatSession(
        user_id=test_user.id,
        title="Quick Assistant",
        session_type="assistant_quick",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    assistant_calls: list[tuple[int, int, str, str]] = []
    standard_calls: list[tuple[int, int, str]] = []

    async def _fake_process_assistant_turn_async(
        session_id: int,
        message_id: int,
        prompt: str,
        *,
        screen_context,
        source: str = "assistant",
    ) -> None:
        assistant_calls.append((session_id, message_id, prompt, screen_context.screen_type))

    async def _fake_process_message_async(session_id: int, message_id: int, prompt: str) -> None:
        standard_calls.append((session_id, message_id, prompt))

    monkeypatch.setattr(
        "app.routers.api.chat.process_assistant_turn_async",
        _fake_process_assistant_turn_async,
    )
    monkeypatch.setattr("app.routers.api.chat.process_message_async", _fake_process_message_async)
    response = client.post(
        f"/api/content/chat/sessions/{session.id}/messages",
        json={"message": "Add a few related feeds."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "processing"
    assert assistant_calls == [
        (
            session.id,
            payload["message_id"],
            "Add a few related feeds.",
            "assistant_quick",
        )
    ]
    assert standard_calls == []


def test_message_status_returns_distinct_assistant_display_id(
    client: TestClient,
    db_session: Session,
    test_user,
) -> None:
    """Completed async status should not reuse the pending user message ID."""

    session = ChatSession(
        user_id=test_user.id,
        title="Quick Assistant",
        session_type="assistant_quick",
        llm_model="openai:gpt-5.4",
        llm_provider="openai",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    payload = json.dumps(
        [
            {
                "parts": [
                    {
                        "content": "What is my favorite article?",
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
                        "content": (
                            "Your most recently favorited article is "
                            "AI Infrastructure Update."
                        ),
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

    db_message = ChatMessage(
        session_id=session.id,
        message_list=payload,
        render_metadata={
            "feed_options": [
                {
                    "id": "8f7d2c42b0c1de90",
                    "title": "lucumr",
                    "site_url": "https://lucumr.pocoo.org/",
                    "feed_url": "https://lucumr.pocoo.org/feed.atom",
                    "feed_type": "atom",
                    "feed_format": "atom",
                    "description": "Armin Ronacher's weblog.",
                    "rationale": "Validated Atom feed.",
                    "evidence_url": "https://lucumr.pocoo.org/",
                }
            ]
        },
        status="completed",
    )
    db_session.add(db_message)
    db_session.commit()
    db_session.refresh(db_message)

    response = client.get(f"/api/content/chat/messages/{db_message.id}/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["message_id"] == db_message.id
    assert payload["assistant_message"]["id"] != db_message.id
    assert payload["assistant_message"]["role"] == "assistant"
    assert "AI Infrastructure Update" in payload["assistant_message"]["content"]
    assert payload["assistant_message"]["feed_options"][0]["title"] == "lucumr"
