"""Tests for API content visibility rules."""
from sqlalchemy.orm import Session

from app.constants import CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content, ContentStatusEntry


def _news_summary_payload(title: str) -> dict[str, object]:
    return {
        "title": title,
        "article_url": "https://processed.com/story",
        "key_points": [
            "Headline takeaway",
            "Secondary insight",
        ],
        "summary": "Short overview of the processed item.",
        "classification": "to_read",
        "summarization_date": "2025-09-23T00:00:00Z",
    }


def _article_summary_payload(title: str) -> dict[str, object]:
    return {
        "title": title,
        "overview": (
            "This overview is long enough to satisfy the minimum length requirement "
            "for structured summaries."
        ),
        "bullet_points": [
            {"text": "Key point one", "category": "key_finding"},
            {"text": "Key point two", "category": "methodology"},
            {"text": "Key point three", "category": "conclusion"},
        ],
        "quotes": [],
        "topics": ["Testing"],
        "summarization_date": "2025-12-31T00:00:00Z",
    }


def test_api_excludes_unprocessed_news(client, db_session: Session, test_user):
    """Unprocessed news items should not appear in the API feed."""
    pending_news = Content(
        content_type="news",
        url="https://example.com/pending",
        title="Pending Cluster",
        status="new",
        content_metadata={
            "platform": "techmeme",
            "source": "example.com",
            "article": {
                "url": "https://example.com/pending",
                "title": "Pending Article",
            },
            "aggregator": {
                "name": "Techmeme",
            },
            "discussion_url": "https://www.techmeme.com/cluster/pending",
        },
    )

    completed_news = Content(
        content_type="news",
        url="https://processed.com/story",
        title="Processed Cluster",
        status="completed",
        content_metadata={
            "platform": "techmeme",
            "source": "processed.com",
            "article": {
                "url": "https://processed.com/story",
                "title": "Processed Article",
            },
            "aggregator": {
                "name": "Techmeme",
            },
            "discussion_url": "https://www.techmeme.com/cluster/processed",
            "summary": _news_summary_payload("Processed Digest"),
            "summary_kind": "short_news_digest",
            "summary_version": 1,
        },
    )

    db_session.add_all([pending_news, completed_news])
    db_session.commit()
    db_session.refresh(pending_news)
    db_session.refresh(completed_news)
    db_session.add_all(
        [
            ContentStatusEntry(
                user_id=test_user.id,
                content_id=pending_news.id,
                status="inbox",
            ),
            ContentStatusEntry(
                user_id=test_user.id,
                content_id=completed_news.id,
                status="inbox",
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/content/?content_type=news&read_filter=unread")
    assert response.status_code == 200

    payload = response.json()
    ids = [item["id"] for item in payload["contents"]]

    assert completed_news.id in ids
    assert pending_news.id not in ids
    assert payload["meta"]["total"] == len(payload["contents"]) == 1


def test_api_excludes_inbox_content_not_completed(client, db_session: Session, test_user):
    """Non-completed inbox items should not appear in content lists."""
    processing_article = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/processing-article",
        title="Processing Article",
        status=ContentStatus.PROCESSING.value,
        content_metadata={
            "summary": _article_summary_payload("Processing Article"),
            "summary_kind": "long_structured",
            "summary_version": 1,
            "image_generated_at": "2025-12-31T00:00:00Z",
        },
    )
    completed_article = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/completed-article",
        title="Completed Article",
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "summary": _article_summary_payload("Completed Article"),
            "summary_kind": "long_structured",
            "summary_version": 1,
            "image_generated_at": "2025-12-31T00:00:00Z",
        },
    )

    db_session.add_all([processing_article, completed_article])
    db_session.commit()
    db_session.refresh(processing_article)
    db_session.refresh(completed_article)

    db_session.add_all(
        [
            ContentStatusEntry(
                user_id=test_user.id,
                content_id=processing_article.id,
                status="inbox",
            ),
            ContentStatusEntry(
                user_id=test_user.id,
                content_id=completed_article.id,
                status="inbox",
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/content/", params={"content_type": "article"})
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["contents"]}

    assert completed_article.id in ids
    assert processing_article.id not in ids


def test_api_excludes_digest_only_news(client, db_session: Session, test_user):
    """Digest-only X news should stay out of normal API feeds."""
    hidden_digest_item = Content(
        content_type="news",
        url="https://x.com/test/status/1#newsly-digest-user-1",
        source_url="https://x.com/test/status/1",
        title="Hidden X item",
        status="completed",
        content_metadata={
            "digest_visibility": CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY,
            "summary": _news_summary_payload("Hidden X item"),
            "summary_kind": "short_news_digest",
            "summary_version": 1,
        },
    )

    db_session.add(hidden_digest_item)
    db_session.commit()

    response = client.get("/api/content/", params={"content_type": "news"})
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["contents"]}

    assert hidden_digest_item.id not in ids
