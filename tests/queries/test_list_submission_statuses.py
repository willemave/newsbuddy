"""Tests for submission status query orchestration."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.contracts import ContentStatus, ContentType
from app.queries import list_submission_statuses


def test_list_submission_statuses_filters_and_shapes_rows(
    db_session: Session,
    content_factory,
    test_user,
    user_factory,
) -> None:
    other_user = user_factory(
        apple_id="other_query_user",
        email="other-query@example.com",
        full_name="Other Query User",
    )
    processing = content_factory(
        url="https://example.com/query-processing",
        source_url="https://example.com/query-processing",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.PROCESSING.value,
        title="Processing Item",
        content_metadata={
            "processing": {
                "submitted_by_user_id": test_user.id,
                "submitted_via": "share_sheet",
            }
        },
    )
    completed = content_factory(
        url="https://example.com/query-completed",
        source_url="https://example.com/query-completed",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        title="Completed Item",
        content_metadata={
            "processing": {
                "submitted_by_user_id": test_user.id,
                "submitted_via": "share_sheet",
            }
        },
    )
    other_user_item = content_factory(
        url="https://example.com/query-other",
        source_url="https://example.com/query-other",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.PROCESSING.value,
        title="Other User Item",
        content_metadata={
            "processing": {
                "submitted_by_user_id": other_user.id,
                "submitted_via": "share_sheet",
            }
        },
    )

    response = list_submission_statuses.execute(
        db_session,
        user_id=test_user.id,
        cursor=None,
        limit=10,
    )

    ids = {item.id for item in response.submissions}
    assert processing.id in ids
    assert completed.id not in ids
    assert other_user_item.id not in ids
    item = next(item for item in response.submissions if item.id == processing.id)
    assert item.submitted_via == "share_sheet"
    assert item.is_self_submission is True


@pytest.mark.parametrize(
    ("subscription_status", "expected_outcome"),
    [
        ("created", "subscribed"),
        ("already_exists", "already_subscribed"),
        ("no_feed_found", "feed_not_found"),
        ("fetch_failed", "feed_fetch_failed"),
        ("unsupported_feed_type", "feed_subscription_failed"),
    ],
)
def test_list_submission_statuses_maps_feed_subscription_outcomes(
    db_session: Session,
    content_factory,
    test_user,
    subscription_status: str,
    expected_outcome: str,
) -> None:
    content = content_factory(
        url=f"https://example.com/{subscription_status}",
        source_url=f"https://example.com/{subscription_status}",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.SKIPPED.value,
        title="Example Feed Request",
        content_metadata={
            "processing": {
                "submitted_by_user_id": test_user.id,
                "submitted_via": "share_sheet",
                "subscribe_to_feed": True,
                "detected_feed": {
                    "url": "https://example.com/feed.xml",
                    "type": "atom",
                    "title": "Example Feed",
                    "format": "rss",
                },
                "feed_subscription": {
                    "status": subscription_status,
                    "feed_url": "https://example.com/feed.xml",
                    "feed_type": "atom",
                    "created": subscription_status == "created",
                    "config_id": 42 if subscription_status == "created" else None,
                    "initial_download": {
                        "requested_count": 2,
                        "ran": subscription_status == "created",
                        "status": "completed" if subscription_status == "created" else "skipped",
                        "saved": 3 if subscription_status == "created" else 0,
                    },
                },
            }
        },
    )

    response = list_submission_statuses.execute(
        db_session,
        user_id=test_user.id,
        cursor=None,
        limit=10,
    )

    item = next(item for item in response.submissions if item.id == content.id)
    assert item.submission_kind == "feed_subscription"
    assert item.outcome == expected_outcome
    assert item.detected_feed is not None
    assert item.detected_feed.url == "https://example.com/feed.xml"
    assert item.feed_subscription is not None
    assert item.feed_subscription.status == subscription_status
    assert item.feed_subscription.initial_download is not None
    assert item.feed_subscription.initial_download.requested_count == 2


def test_list_submission_statuses_keeps_generic_skipped_content_as_skipped(
    db_session: Session,
    content_factory,
    test_user,
) -> None:
    content = content_factory(
        url="https://example.com/generic-skipped",
        source_url="https://example.com/generic-skipped",
        content_type=ContentType.UNKNOWN.value,
        status=ContentStatus.SKIPPED.value,
        title="Generic Skipped Item",
        content_metadata={
            "processing": {
                "submitted_by_user_id": test_user.id,
                "submitted_via": "share_sheet",
            }
        },
    )

    response = list_submission_statuses.execute(
        db_session,
        user_id=test_user.id,
        cursor=None,
        limit=10,
    )

    item = next(item for item in response.submissions if item.id == content.id)
    assert item.submission_kind == "content"
    assert item.outcome == "skipped"
    assert item.detected_feed is None
    assert item.feed_subscription is None
