"""Tests for tweet suggestions API endpoint."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content
from app.services.tweet_suggestions import (
    TWEET_MODEL,
    TweetSuggestionData,
    TweetSuggestionsResult,
)

TWEET_GENERATOR_PATCH_TARGET = (
    "app.commands.generate_tweet_suggestions.generate_tweet_suggestions"
)


def test_tweet_suggestions_success(client: TestClient, db_session: Session) -> None:
    """Test successful tweet suggestion generation."""
    article = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Test Article",
        source="Tech Blog",
        content_metadata={
            "source": "Tech Blog",
            "summary": {
                "title": "Great Article",
                "overview": (
                    "This is an overview that is long enough to pass validation "
                    "requirements for the structured summary."
                ),
                "bullet_points": [
                    {"text": "Key point one", "category": "key_finding"},
                    {"text": "Key point two", "category": "methodology"},
                    {"text": "Key point three", "category": "conclusion"},
                ],
                "quotes": [],
                "topics": ["Testing"],
            },
            "summary_kind": "long_structured",
            "summary_version": 1,
        },
    )
    db_session.add(article)
    db_session.commit()
    db_session.refresh(article)

    mock_result = TweetSuggestionsResult(
        content_id=article.id,
        creativity=5,
        length="medium",
        model=TWEET_MODEL,
        suggestions=[
            TweetSuggestionData(id=1, text="Tweet 1", style_label="insightful"),
            TweetSuggestionData(id=2, text="Tweet 2", style_label="provocative"),
            TweetSuggestionData(id=3, text="Tweet 3", style_label="reflective"),
        ],
    )

    with patch(TWEET_GENERATOR_PATCH_TARGET, return_value=mock_result):
        response = client.post(
            f"/api/content/{article.id}/tweet-suggestions",
            json={"creativity": 5},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["content_id"] == article.id
    assert data["creativity"] == 5
    assert data["model"] == TWEET_MODEL
    assert len(data["suggestions"]) == 3
    assert data["suggestions"][0]["text"] == "Tweet 1"
    assert data["suggestions"][0]["style_label"] == "insightful"


def test_tweet_suggestions_with_message(client: TestClient, db_session: Session) -> None:
    """Test tweet generation with user message/guidance."""
    article = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Test Article",
        content_metadata={
            "summary": {
                "title": "Article Title",
                "overview": (
                    "This overview is intentionally long enough to satisfy validation "
                    "requirements and provide realistic context for testing."
                ),
                "bullet_points": [
                    {"text": "Key point one", "category": "key_finding"},
                    {"text": "Key point two", "category": "methodology"},
                    {"text": "Key point three", "category": "conclusion"},
                ],
                "quotes": [],
                "topics": ["Testing"],
            },
            "summary_kind": "long_structured",
            "summary_version": 1,
        },
    )
    db_session.add(article)
    db_session.commit()
    db_session.refresh(article)

    mock_result = TweetSuggestionsResult(
        content_id=article.id,
        creativity=7,
        length="medium",
        model=TWEET_MODEL,
        suggestions=[
            TweetSuggestionData(id=1, text="Startup focused tweet", style_label="a"),
            TweetSuggestionData(id=2, text="Another startup tweet", style_label="b"),
            TweetSuggestionData(id=3, text="Third startup tweet", style_label="c"),
        ],
    )

    with patch(TWEET_GENERATOR_PATCH_TARGET, return_value=mock_result) as mock_gen:
        response = client.post(
            f"/api/content/{article.id}/tweet-suggestions",
            json={
                "message": "focus on startup implications",
                "creativity": 7,
            },
        )

        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs["message"] == "focus on startup implications"
        assert call_kwargs["creativity"] == 7

    assert response.status_code == 200


def test_tweet_suggestions_content_not_found(client: TestClient, db_session: Session) -> None:
    """Test 404 for non-existent content."""
    response = client.post(
        "/api/content/99999/tweet-suggestions",
        json={"creativity": 5},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_tweet_suggestions_content_not_completed(client: TestClient, db_session: Session) -> None:
    """Test 400 for content that's not completed."""
    article = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.NEW.value,
        title="Test Article",
    )
    db_session.add(article)
    db_session.commit()
    db_session.refresh(article)

    response = client.post(
        f"/api/content/{article.id}/tweet-suggestions",
        json={"creativity": 5},
    )

    assert response.status_code == 400
    assert "not ready" in response.json()["detail"].lower()


def test_tweet_suggestions_podcast_supported(client: TestClient, db_session: Session) -> None:
    """Podcasts are now supported for tweet generation."""
    podcast = Content(
        url="https://example.com/podcast",
        content_type=ContentType.PODCAST.value,
        status=ContentStatus.COMPLETED.value,
        title="Test Podcast",
        content_metadata={
            "summary": {
                "title": "Podcast Episode",
                "overview": "Summary of the podcast episode that is long enough for validation.",
                "bullet_points": [
                    {"text": "Key point one", "category": "key_finding"},
                    {"text": "Key point two", "category": "methodology"},
                    {"text": "Key point three", "category": "conclusion"},
                ],
                "quotes": [],
                "topics": ["Testing"],
            },
            "summary_kind": "long_structured",
            "summary_version": 1,
        },
    )
    db_session.add(podcast)
    db_session.commit()
    db_session.refresh(podcast)

    mock_result = TweetSuggestionsResult(
        content_id=podcast.id,
        creativity=5,
        length="medium",
        model=TWEET_MODEL,
        suggestions=[
            TweetSuggestionData(id=1, text="Podcast tweet 1", style_label="a"),
            TweetSuggestionData(id=2, text="Podcast tweet 2", style_label="b"),
            TweetSuggestionData(id=3, text="Podcast tweet 3", style_label="c"),
        ],
    )

    with patch(
        "app.commands.generate_tweet_suggestions.generate_tweet_suggestions",
        return_value=mock_result,
    ):
        response = client.post(
            f"/api/content/{podcast.id}/tweet-suggestions",
            json={"creativity": 5},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["content_id"] == podcast.id


def test_tweet_suggestions_creativity_out_of_range(client: TestClient, db_session: Session) -> None:
    """Test 422 for creativity values outside valid range."""
    article = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Test Article",
    )
    db_session.add(article)
    db_session.commit()
    db_session.refresh(article)

    response = client.post(
        f"/api/content/{article.id}/tweet-suggestions",
        json={"creativity": 0},
    )
    assert response.status_code == 422

    response = client.post(
        f"/api/content/{article.id}/tweet-suggestions",
        json={"creativity": 15},
    )
    assert response.status_code == 422


def test_tweet_suggestions_llm_failure(client: TestClient, db_session: Session) -> None:
    """Test 502 when LLM generation fails."""
    article = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Test Article",
        content_metadata={
            "summary": {
                "title": "Article Title",
                "overview": (
                    "This overview is intentionally long enough to satisfy validation "
                    "requirements and provide realistic context for testing."
                ),
                "bullet_points": [
                    {"text": "Key point one", "category": "key_finding"},
                    {"text": "Key point two", "category": "methodology"},
                    {"text": "Key point three", "category": "conclusion"},
                ],
                "quotes": [],
                "topics": ["Testing"],
            },
            "summary_kind": "long_structured",
            "summary_version": 1,
        },
    )
    db_session.add(article)
    db_session.commit()
    db_session.refresh(article)

    with patch(TWEET_GENERATOR_PATCH_TARGET, return_value=None):
        response = client.post(
            f"/api/content/{article.id}/tweet-suggestions",
            json={"creativity": 5},
        )

    assert response.status_code == 502
    assert "failed" in response.json()["detail"].lower()


def test_tweet_suggestions_news_content(client: TestClient, db_session: Session) -> None:
    """Test tweet generation works for news content type."""
    news = Content(
        url="https://example.com/article",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.COMPLETED.value,
        title="HN Discussion",
        content_metadata={
            "article": {
                "url": "https://example.com/article",
                "title": "The Article",
            },
            "discussion_url": "https://news.ycombinator.com/item?id=12345",
            "summary": {
                "title": "News Summary",
                "summary": "Overview of the news",
                "key_points": ["Point 1"],
            },
            "summary_kind": "short_news_digest",
            "summary_version": 1,
        },
    )
    db_session.add(news)
    db_session.commit()
    db_session.refresh(news)

    mock_result = TweetSuggestionsResult(
        content_id=news.id,
        creativity=5,
        length="medium",
        model=TWEET_MODEL,
        suggestions=[
            TweetSuggestionData(id=1, text="News tweet 1", style_label="a"),
            TweetSuggestionData(id=2, text="News tweet 2", style_label="b"),
            TweetSuggestionData(id=3, text="News tweet 3", style_label="c"),
        ],
    )

    with patch(TWEET_GENERATOR_PATCH_TARGET, return_value=mock_result):
        response = client.post(
            f"/api/content/{news.id}/tweet-suggestions",
            json={"creativity": 5},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["content_id"] == news.id


def test_tweet_suggestions_default_creativity(client: TestClient, db_session: Session) -> None:
    """Test that default creativity (5) is used when not provided."""
    article = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Test Article",
        content_metadata={
            "summary": {
                "title": "Article Title",
                "overview": (
                    "This overview is intentionally long enough to satisfy validation "
                    "requirements and provide realistic context for testing."
                ),
                "bullet_points": [
                    {"text": "Key point one", "category": "key_finding"},
                    {"text": "Key point two", "category": "methodology"},
                    {"text": "Key point three", "category": "conclusion"},
                ],
                "quotes": [],
                "topics": ["Testing"],
            },
            "summary_kind": "long_structured",
            "summary_version": 1,
        },
    )
    db_session.add(article)
    db_session.commit()
    db_session.refresh(article)

    mock_result = TweetSuggestionsResult(
        content_id=article.id,
        creativity=5,
        length="medium",
        model=TWEET_MODEL,
        suggestions=[
            TweetSuggestionData(id=1, text="Tweet 1", style_label="a"),
            TweetSuggestionData(id=2, text="Tweet 2", style_label="b"),
            TweetSuggestionData(id=3, text="Tweet 3", style_label="c"),
        ],
    )

    with patch(TWEET_GENERATOR_PATCH_TARGET, return_value=mock_result) as mock_gen:
        response = client.post(
            f"/api/content/{article.id}/tweet-suggestions",
            json={},
        )

        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs["creativity"] == 5

    assert response.status_code == 200
