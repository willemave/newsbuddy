import os
from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import get_readonly_db_session
from app.core.deps import get_current_user
from app.models.chat_message_metadata import AssistantFeedOption, AssistantFeedOptionsResult
from app.models.schema import Base, Content, ContentStatusEntry
from app.models.user import User

# Import router and models without importing app.main (avoids env/settings side effects)
from app.routers import api_content


@pytest.fixture(scope="module")
def test_app() -> Generator[FastAPI]:
    # Set a safe DATABASE_URL for any code that might read it (defensive)
    os.environ.setdefault("DATABASE_URL", "sqlite://")

    app = FastAPI()
    app.include_router(api_content.router, prefix="/api/content")
    yield app


@pytest.fixture(scope="module")
def db_session() -> Generator[Session]:
    # In-memory SQLite shared across the module
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="module")
def test_user(db_session: Session) -> User:
    user = User(
        apple_id="test-user-1",
        email="test-user@example.com",
        full_name="Test User",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    seed_content(db_session, user)
    return user


@pytest.fixture(scope="module")
def client(test_app: FastAPI, db_session: Session, test_user: User) -> Generator[TestClient]:
    # Override DB dependency for router endpoints
    def _get_db_session_override() -> Session:
        return db_session

    def _get_current_user_override() -> User:
        return test_user

    test_app.dependency_overrides[get_readonly_db_session] = _get_db_session_override
    test_app.dependency_overrides[get_current_user] = _get_current_user_override
    with TestClient(test_app) as c:
        yield c


def seed_content(db: Session, user: User):
    items = [
        Content(
            content_type="article",
            url="https://example.com/ai-article",
            title="Understanding AI in 2025",
            source="Tech Blog",
            platform="substack",
            status="completed",
            content_metadata={
                "summary": {
                    "title": "Understanding AI in 2025",
                    "overview": (
                        "Deep dive into artificial intelligence and its evolution across "
                        "research, product, and policy landscapes in 2025."
                    ),
                    "bullet_points": [
                        {
                            "text": "AI systems are improving across multi-modal tasks.",
                            "category": "key_finding",
                        },
                        {
                            "text": "Deployment practices emphasize safety and monitoring.",
                            "category": "methodology",
                        },
                        {
                            "text": "Regulators are aligning on AI risk frameworks.",
                            "category": "context",
                        },
                    ],
                    "topics": ["AI", "Policy", "Product"],
                },
                "image_generated_at": "2025-01-01T00:00:00Z",
            },
        ),
        Content(
            content_type="podcast",
            url="https://example.com/podcast-ep1",
            title="Tech Talk Episode 1",
            source="Tech Podcast",
            platform="youtube",
            status="completed",
            content_metadata={
                "transcript": "Today we discuss machine learning and AI systems",
                "summary": {
                    "title": "Tech Talk Episode 1",
                    "overview": "Discussion about machine learning",
                },
            },
        ),
        # Skipped item should not appear in results
        Content(
            content_type="article",
            url="https://example.com/skip-me",
            title="Skip This",
            source="Misc",
            classification="skip",
            status="completed",
            content_metadata={
                "summary": {"title": "Skip This", "overview": "Not relevant"}
            },
        ),
    ]
    for it in items:
        db.add(it)
    db.commit()
    for it in items:
        db.add(
            ContentStatusEntry(
                user_id=user.id,
                content_id=it.id,
                status="inbox",
            )
        )
    db.commit()


class TestSearchAPI:
    def test_search_basic(self, client: TestClient, db_session: Session):
        r = client.get("/api/content/search", params={"q": "AI"})
        assert r.status_code == 200
        data = r.json()
        assert data["meta"]["total"] >= 1
        # Ensure 'skip' item isn't present
        for c in data["contents"]:
            assert c["title"] != "Skip This"

    def test_search_type_filter(self, client: TestClient):
        r = client.get("/api/content/search", params={"q": "tech", "type": "article"})
        assert r.status_code == 200
        data = r.json()
        for c in data["contents"]:
            assert c["content_type"] == "article"

    def test_search_pagination(self, client: TestClient):
        r = client.get("/api/content/search", params={"q": "tech", "limit": 1, "offset": 0})
        assert r.status_code == 200
        data = r.json()
        assert len(data["contents"]) <= 1

    def test_search_validation(self, client: TestClient):
        # Too short
        r = client.get("/api/content/search", params={"q": "a"})
        assert r.status_code == 422
        # Invalid type
        r = client.get("/api/content/search", params={"q": "ai", "type": "video"})
        assert r.status_code == 422

    def test_mixed_search_returns_sectioned_results(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(
            "app.routers.api.content_list.find_feed_options",
            lambda query, limit: AssistantFeedOptionsResult(
                query=query,
                options=[
                    AssistantFeedOption(
                        id="feed-option-0001",
                        title="AI Weekly",
                        site_url="https://ai.example.com",
                        feed_url="https://ai.example.com/feed.xml",
                        feed_type="atom",
                        feed_format="rss",
                        description="AI coverage",
                        rationale="Validated feed",
                        evidence_url="https://ai.example.com",
                    )
                ],
            ),
        )
        monkeypatch.setattr(
            "app.routers.api.content_list.search_podcast_episodes",
            lambda query, limit: [
                type(
                    "PodcastHit",
                    (),
                    {
                        "title": "AI Weekly Episode",
                        "episode_url": "https://podcasts.example.com/episodes/1",
                        "podcast_title": "AI Weekly",
                        "source": "example.fm",
                        "snippet": "Episode summary",
                        "feed_url": "https://podcasts.example.com/feed.xml",
                        "published_at": "2026-02-19T00:00:00Z",
                        "provider": "listen_notes",
                        "score": 1.0,
                    },
                )()
            ],
        )

        response = client.get("/api/content/search/mixed", params={"q": "AI", "limit": 5})

        assert response.status_code == 200
        payload = response.json()
        assert payload["query"] == "AI"
        assert payload["content"]
        assert payload["feeds"][0]["feed_url"] == "https://ai.example.com/feed.xml"
        assert payload["podcasts"][0]["episode_url"] == "https://podcasts.example.com/episodes/1"
