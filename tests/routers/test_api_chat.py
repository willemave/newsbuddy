"""Tests for chat session API endpoints."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import ChatSession, Content


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
    assert session["session_type"] == "article_brain"
    assert session["article_title"] == "Test Article About AI"

    # Verify session in database
    db_session_record = (
        db_session.query(ChatSession).filter(ChatSession.id == session["id"]).first()
    )
    assert db_session_record is not None
    assert db_session_record.user_id == test_user.id


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
    assert session["session_type"] == "topic"
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
    assert session["session_type"] == "ad_hoc"
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
            session_type="article_brain" if i == 0 else "ad_hoc",
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
