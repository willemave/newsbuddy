"""Tests for recently read content endpoints."""

from datetime import UTC, datetime, timedelta


def test_recently_read_scoped_to_user(
    client,
    content_factory,
    knowledge_save_factory,
    read_status_factory,
    test_user,
    user_factory,
) -> None:
    """Ensure recently read list only includes the current user's reads."""
    other_user = user_factory(
        apple_id="other_apple_id",
        email="other@example.com",
        full_name="Other User",
    )
    content_one = content_factory(
        url="https://example.com/read-by-current",
        title="Read by Current User",
        content_metadata={
            "summary": {
                "title": "Read by Current User",
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
            },
            "summary_kind": "long_structured",
            "summary_version": 1,
            "image_generated_at": "2026-01-01T00:00:00Z",
        },
    )
    content_two = content_factory(
        url="https://example.com/read-by-other",
        title="Read by Other User",
        content_metadata={
            "summary": {
                "title": "Read by Other User",
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
            },
            "summary_kind": "long_structured",
            "summary_version": 1,
            "image_generated_at": "2026-01-01T00:00:00Z",
        },
    )

    timestamp = datetime.now(UTC)
    read_status_factory(
        user=test_user,
        content=content_one,
        read_at=timestamp,
        created_at=timestamp,
    )
    read_status_factory(
        user=other_user,
        content=content_two,
        read_at=timestamp,
        created_at=timestamp,
    )
    knowledge_save_factory(
        user=other_user,
        content=content_one,
        saved_at=timestamp,
        created_at=timestamp,
    )

    response = client.get("/api/content/recently-read/list")
    assert response.status_code == 200

    payload = response.json()
    ids = {item["id"] for item in payload["contents"]}
    assert content_one.id in ids
    assert content_two.id not in ids

    item = next(entry for entry in payload["contents"] if entry["id"] == content_one.id)
    assert item["is_read"] is True
    assert item["is_saved_to_knowledge"] is False


def test_recently_read_filters_by_content_type_and_read_date(
    client,
    content_factory,
    read_status_factory,
    test_user,
) -> None:
    """Recently-read filters should affect the backend query, not just the local UI."""
    today = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    yesterday = today - timedelta(days=1)

    article = content_factory(
        url="https://example.com/recent-article",
        title="Recent Article",
        content_type="article",
    )
    podcast = content_factory(
        url="https://example.com/recent-podcast",
        title="Recent Podcast",
        content_type="podcast",
    )
    older_podcast = content_factory(
        url="https://example.com/older-podcast",
        title="Older Podcast",
        content_type="podcast",
    )

    read_status_factory(user=test_user, content=article, read_at=today, created_at=today)
    read_status_factory(user=test_user, content=podcast, read_at=today, created_at=today)
    read_status_factory(
        user=test_user,
        content=older_podcast,
        read_at=yesterday,
        created_at=yesterday,
    )

    response = client.get(
        "/api/content/recently-read/list",
        params={
            "content_type": "podcast",
            "date": today.date().isoformat(),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload["contents"]] == [podcast.id]
    assert today.date().isoformat() in payload["available_dates"]


def test_recently_read_rejects_invalid_read_date(client) -> None:
    response = client.get(
        "/api/content/recently-read/list",
        params={"date": "2026-02-30"},
    )

    assert response.status_code == 422
