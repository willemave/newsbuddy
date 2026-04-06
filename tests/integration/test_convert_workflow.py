"""Integration tests for news-to-article conversion workflow."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content, ContentStatusEntry
from app.models.user import User
from app.repositories import favorites_repository


def test_full_convert_workflow(client: TestClient, db_session: Session) -> None:
    """Test complete workflow: create news → convert → verify article."""
    # 1. Create news item with article URL
    news = Content(
        url="https://techblog.example/future-of-ai",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "article": {
                "url": "https://techblog.example/future-of-ai",
                "title": "The Future of AI",
                "source_domain": "techblog.example",
            },
            "discussion_url": "https://news.ycombinator.com/item?id=99999",
            "summary": {
                "title": "AI Discussion on HN",
                "summary": (
                    "Interesting discussion about AI trends that highlights recent advancements"
                ),
                "key_points": ["AI is evolving rapidly", "New models are more efficient"],
            },
            "summary_kind": "short_news_digest",
            "summary_version": 1,
        },
    )
    db_session.add(news)
    db_session.commit()
    db_session.refresh(news)

    # 2. Convert news to article
    convert_response = client.post(f"/api/content/{news.id}/convert-to-article")
    assert convert_response.status_code == 200

    convert_data = convert_response.json()
    assert convert_data["status"] == "success"
    assert convert_data["already_exists"] is False
    new_article_id = convert_data["new_content_id"]

    # 3. Verify article was created correctly
    article = db_session.query(Content).filter(Content.id == new_article_id).first()
    assert article is not None
    assert article.content_type == ContentType.ARTICLE.value
    assert article.url == "https://techblog.example/future-of-ai"
    assert article.title == "The Future of AI"
    assert article.source == "techblog.example"
    assert article.status == ContentStatus.PENDING.value

    # 4. Verify article appears in content list via database
    all_articles = (
        db_session.query(Content).filter(Content.content_type == ContentType.ARTICLE.value).all()
    )
    article_ids = [a.id for a in all_articles]
    assert new_article_id in article_ids, (
        f"New article {new_article_id} not found in articles {article_ids}"
    )

    # 5. Try converting same news again - should return existing article
    convert_again = client.post(f"/api/content/{news.id}/convert-to-article")
    assert convert_again.status_code == 200

    convert_again_data = convert_again.json()
    assert convert_again_data["already_exists"] is True
    assert convert_again_data["new_content_id"] == new_article_id


def test_convert_marks_news_as_favorite_interaction(
    client: TestClient, db_session: Session, test_user: User
) -> None:
    """Test that converting news saves the resulting article to knowledge."""
    # Create news item
    news = Content(
        url="https://example.com/article",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "article": {"url": "https://example.com/article"},
            "discussion_url": "https://news.ycombinator.com/item?id=88888",
        },
    )
    db_session.add(news)
    db_session.commit()
    db_session.refresh(news)
    db_session.add(ContentStatusEntry(user_id=test_user.id, content_id=news.id, status="inbox"))
    db_session.commit()

    # Favorite the news
    fav_response = client.post(f"/api/content/{news.id}/favorite")
    assert fav_response.status_code == 200
    assert fav_response.json()["is_favorited"] is True

    # Convert to article
    convert_response = client.post(f"/api/content/{news.id}/convert-to-article")
    assert convert_response.status_code == 200

    new_article_id = convert_response.json()["new_content_id"]
    db_session.add(
        ContentStatusEntry(user_id=test_user.id, content_id=new_article_id, status="inbox")
    )
    db_session.commit()

    # Verify news is still favorited
    news_detail = client.get(f"/api/content/{news.id}")
    assert news_detail.json()["is_favorited"] is True

    assert (
        favorites_repository.is_content_favorited(db_session, new_article_id, test_user.id) is True
    )
