"""Tests for news link to article conversion endpoint."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content
from app.repositories import favorites_repository


def test_convert_news_link_to_article(
    client: TestClient,
    db_session: Session,
    test_user,
) -> None:
    """Test converting a news link to a full article."""
    # Create a news item with article URL
    news = Content(
        url="https://example.com/article",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "article": {
                "url": "https://example.com/article",
                "title": "Test Article",
                "source_domain": "example.com",
            },
            "discussion_url": "https://news.ycombinator.com/item?id=12345",
            "summary": {"title": "News Summary", "summary": "This is a news summary"},
            "summary_kind": "short_news_digest",
            "summary_version": 1,
        },
    )
    db_session.add(news)
    db_session.commit()
    db_session.refresh(news)

    # Convert to article
    response = client.post(f"/api/content/{news.id}/convert-to-article")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"
    assert "new_content_id" in data
    assert data["original_content_id"] == news.id

    # Verify new article was created
    new_article = db_session.query(Content).filter(Content.id == data["new_content_id"]).first()
    assert new_article is not None
    assert new_article.content_type == ContentType.ARTICLE.value
    assert new_article.url == "https://example.com/article"
    assert new_article.status == ContentStatus.PENDING.value
    assert (
        favorites_repository.is_content_favorited(db_session, new_article.id, test_user.id) is True
    )


def test_convert_news_link_no_article_url(client: TestClient, db_session: Session) -> None:
    """Test converting news link without article URL fails gracefully."""
    news = Content(
        url="twitter://list/example",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "summary": {"title": "News Summary"},
            "summary_kind": "short_news_digest",
            "summary_version": 1,
        },
    )
    db_session.add(news)
    db_session.commit()
    db_session.refresh(news)

    response = client.post(f"/api/content/{news.id}/convert-to-article")
    assert response.status_code == 400
    assert "no article url" in response.json()["detail"].lower()


def test_convert_non_news_content(client: TestClient, db_session: Session) -> None:
    """Test that converting non-news content fails."""
    article = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
    )
    db_session.add(article)
    db_session.commit()
    db_session.refresh(article)

    response = client.post(f"/api/content/{article.id}/convert-to-article")
    assert response.status_code == 400
    assert "only news" in response.json()["detail"].lower()


def test_convert_already_exists(
    client: TestClient,
    db_session: Session,
    test_user,
) -> None:
    """Test converting when article already exists returns existing ID."""
    article_url = "https://example.com/article"

    # Create existing article
    existing = Content(
        url=article_url,
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
    )
    db_session.add(existing)
    db_session.commit()
    db_session.refresh(existing)

    # Create news item pointing to same URL
    news = Content(
        url=article_url,
        content_type=ContentType.NEWS.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "article": {"url": article_url},
            "discussion_url": "https://news.ycombinator.com/item?id=12345",
        },
    )
    db_session.add(news)
    db_session.commit()
    db_session.refresh(news)

    response = client.post(f"/api/content/{news.id}/convert-to-article")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"
    assert data["new_content_id"] == existing.id
    assert data["already_exists"] is True
    assert favorites_repository.is_content_favorited(db_session, existing.id, test_user.id) is True
