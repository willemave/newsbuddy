"""Tests for content list filtering behavior."""

from __future__ import annotations

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content, ContentStatusEntry


def _build_summary(title: str) -> dict[str, object]:
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


def _build_podcast_summary(title: str) -> dict[str, object]:
    return {
        "title": title,
        "editorial_narrative": (
            "First paragraph with concrete details, entities, timelines, and practical "
            "implications about how teams are deploying AI systems in production today.\n\n"
            "Second paragraph with constraints, tradeoffs, governance concerns, and "
            "implementation detail that gives enough substance to satisfy the schema."
        ),
        "quotes": [
            {"text": "Quote one with enough detail for validation.", "attribution": "Host A"},
            {"text": "Quote two with enough detail for validation.", "attribution": "Guest B"},
        ],
        "key_points": [
            {"point": "Point one with concrete detail."},
            {"point": "Point two with concrete detail."},
            {"point": "Point three with concrete detail."},
            {"point": "Point four with concrete detail."},
        ],
    }


def _add_inbox_status(db_session, user_id: int, content_id: int) -> None:
    db_session.add(
        ContentStatusEntry(
            user_id=user_id,
            content_id=content_id,
            status="inbox",
        )
    )


def test_list_filters_articles_without_keypoints_or_summary(
    client,
    db_session,
    test_user,
) -> None:
    ready_article = Content(
        url="https://example.com/ready",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "summary": _build_summary("Ready Article"),
            "summary_kind": "long_structured",
            "summary_version": 1,
            "image_generated_at": "2025-12-31T00:00:00Z",
        },
    )
    missing_summary = Content(
        url="https://example.com/no-summary",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={"image_generated_at": "2025-12-31T00:00:00Z"},
    )
    missing_image = Content(
        url="https://example.com/no-image",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "summary": _build_summary("No Image"),
            "summary_kind": "long_structured",
            "summary_version": 1,
        },
    )

    db_session.add_all([ready_article, missing_summary, missing_image])
    db_session.commit()
    db_session.refresh(ready_article)
    db_session.refresh(missing_summary)
    db_session.refresh(missing_image)

    _add_inbox_status(db_session, test_user.id, ready_article.id)
    _add_inbox_status(db_session, test_user.id, missing_summary.id)
    _add_inbox_status(db_session, test_user.id, missing_image.id)
    db_session.commit()

    response = client.get("/api/content/", params={"content_type": "article"})
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["contents"]}

    assert ready_article.id in ids
    assert missing_summary.id not in ids
    assert missing_image.id in ids


def test_list_hides_news_images_even_when_metadata_has_urls(
    client,
    db_session,
    test_user,
) -> None:
    news_item = Content(
        url="https://example.com/news-item",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.COMPLETED.value,
        title="News Item",
        content_metadata={
            "summary": {
                "title": "News Item",
                "summary": "Short news summary",
                "classification": "to_read",
            },
            "summary_kind": "short_news_digest",
            "summary_version": 1,
            "article": {"url": "https://example.com/news-item"},
            "image_url": "https://example.com/screenshot.png",
            "thumbnail_url": "https://example.com/screenshot-thumb.png",
            "image_generated_at": "2026-01-01T00:00:00Z",
        },
    )

    db_session.add(news_item)
    db_session.commit()
    db_session.refresh(news_item)

    _add_inbox_status(db_session, test_user.id, news_item.id)
    db_session.commit()

    response = client.get("/api/content/", params={"content_type": "news"})
    assert response.status_code == 200
    payload = response.json()

    returned_item = next(item for item in payload["contents"] if item["id"] == news_item.id)
    assert returned_item["image_url"] is None
    assert returned_item["thumbnail_url"] is None


def test_podcast_uses_provider_thumbnail_as_fallback_when_no_generated_image(
    client,
    db_session,
    test_user,
) -> None:
    podcast = Content(
        url="https://example.com/podcast-fallback",
        content_type=ContentType.PODCAST.value,
        status=ContentStatus.COMPLETED.value,
        title="Podcast Fallback",
        content_metadata={
            "summary": _build_podcast_summary("Podcast Fallback"),
            "summary_kind": "long_editorial_narrative",
            "summary_version": 1,
            "thumbnail_url": "https://cdn.example.com/provider-thumb.png",
            "video_id": "abc123",
        },
    )

    db_session.add(podcast)
    db_session.commit()
    db_session.refresh(podcast)
    _add_inbox_status(db_session, test_user.id, podcast.id)
    db_session.commit()

    response = client.get("/api/content/", params={"content_type": "podcast"})
    assert response.status_code == 200
    payload = response.json()

    returned_item = next(item for item in payload["contents"] if item["id"] == podcast.id)
    assert returned_item["image_url"] == "https://cdn.example.com/provider-thumb.png"
    assert returned_item["thumbnail_url"] is None


def test_podcast_prefers_generated_image_over_provider_thumbnail(
    client,
    db_session,
    test_user,
) -> None:
    podcast = Content(
        url="https://example.com/podcast-generated",
        content_type=ContentType.PODCAST.value,
        status=ContentStatus.COMPLETED.value,
        title="Podcast Generated",
        content_metadata={
            "summary": _build_podcast_summary("Podcast Generated"),
            "summary_kind": "long_editorial_narrative",
            "summary_version": 1,
            "thumbnail_url": "https://cdn.example.com/provider-thumb.png",
            "video_id": "abc123",
            "image_generated_at": "2026-01-01T00:00:00Z",
        },
    )

    db_session.add(podcast)
    db_session.commit()
    db_session.refresh(podcast)
    _add_inbox_status(db_session, test_user.id, podcast.id)
    db_session.commit()

    response = client.get("/api/content/", params={"content_type": "podcast"})
    assert response.status_code == 200
    payload = response.json()

    returned_item = next(item for item in payload["contents"] if item["id"] == podcast.id)
    assert returned_item["image_url"] == f"/static/images/content/{podcast.id}.png"
    assert returned_item["thumbnail_url"] == f"/static/images/thumbnails/{podcast.id}.png"
