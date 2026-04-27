"""Tests for submission status query orchestration."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.metadata import ContentStatus, ContentType
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
