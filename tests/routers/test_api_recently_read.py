"""Tests for recently read content endpoints."""

from datetime import UTC, datetime


def test_recently_read_scoped_to_user(
    client,
    content_factory,
    favorite_factory,
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
    )
    content_two = content_factory(
        url="https://example.com/read-by-other",
        title="Read by Other User",
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
    favorite_factory(
        user=other_user,
        content=content_one,
        favorited_at=timestamp,
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
    assert item["is_favorited"] is False
