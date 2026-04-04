"""Tests for user submission status list endpoint."""

from app.models.metadata import ContentStatus, ContentType


def test_submission_status_list_filters_by_user_and_status(
    client,
    content_factory,
    test_user,
    user_factory,
) -> None:
    other_user = user_factory(
        apple_id="other_apple_id_999",
        email="other@example.com",
        full_name="Other User",
    )

    processing = content_factory(
        url="https://example.com/processing",
        source_url="https://example.com/processing",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.PROCESSING.value,
        title="Processing Item",
        content_metadata={
            "submitted_by_user_id": test_user.id,
            "submitted_via": "share_sheet",
        },
    )
    failed = content_factory(
        url="https://example.com/failed",
        source_url="https://example.com/failed",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.FAILED.value,
        title="Failed Item",
        error_message="Fetch failed",
        content_metadata={
            "submitted_by_user_id": test_user.id,
            "submitted_via": "share_sheet",
        },
    )
    skipped = content_factory(
        url="https://example.com/skipped",
        source_url="https://example.com/skipped",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.SKIPPED.value,
        title="Skipped Item",
        content_metadata={
            "submitted_by_user_id": test_user.id,
            "submitted_via": "share_sheet",
        },
    )
    completed = content_factory(
        url="https://example.com/completed",
        source_url="https://example.com/completed",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Completed Item",
        content_metadata={
            "submitted_by_user_id": test_user.id,
            "submitted_via": "share_sheet",
        },
    )
    other_user_item = content_factory(
        url="https://example.com/other-user",
        source_url="https://example.com/other-user",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.PROCESSING.value,
        title="Other User Item",
        content_metadata={
            "submitted_by_user_id": other_user.id,
            "submitted_via": "share_sheet",
        },
    )

    response = client.get("/api/content/submissions/list")
    assert response.status_code == 200
    payload = response.json()

    ids = {item["id"] for item in payload["submissions"]}
    assert processing.id in ids
    assert failed.id in ids
    assert skipped.id in ids
    assert completed.id not in ids
    assert other_user_item.id not in ids

    failed_item = next(item for item in payload["submissions"] if item["id"] == failed.id)
    assert failed_item["error_message"] == "Fetch failed"
    assert failed_item["is_self_submission"] is True
