"""List processing status for the current user's submitted content."""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, and_, cast, or_
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.api.content import (
    DetectedFeed,
    SubmissionFeedInitialDownloadResponse,
    SubmissionFeedSubscriptionResponse,
    SubmissionKind,
    SubmissionOutcome,
    SubmissionStatusListResponse,
    SubmissionStatusResponse,
)
from app.models.api.pagination import PaginationMetadata
from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content
from app.models.metadata.access import ContentMetadataView, metadata_view
from app.utils.pagination import PaginationCursor

logger = get_logger(__name__)


def execute(
    db: Session,
    *,
    user_id: int,
    cursor: str | None,
    limit: int,
) -> SubmissionStatusListResponse:
    """Return non-completed self-submitted content for one user."""
    last_id = None
    last_created_at = None
    if cursor:
        cursor_data = PaginationCursor.decode_cursor(cursor)
        last_id = cursor_data.last_id
        last_created_at = cursor_data.last_created_at

    status_filter = [
        ContentStatus.NEW.value,
        ContentStatus.PENDING.value,
        ContentStatus.PROCESSING.value,
        ContentStatus.FAILED.value,
        ContentStatus.SKIPPED.value,
    ]
    submitter_filter = or_(
        cast(Content.content_metadata["processing"]["submitted_by_user_id"], String)
        == str(user_id),
        cast(Content.content_metadata["submitted_by_user_id"], String) == str(user_id),
    )

    query = (
        db.query(Content)
        .filter(submitter_filter)
        .filter(Content.status.in_(status_filter))
        .order_by(Content.created_at.desc(), Content.id.desc())
    )

    if last_id and last_created_at:
        query = query.filter(
            or_(
                Content.created_at < last_created_at,
                and_(Content.created_at == last_created_at, Content.id < last_id),
            )
        )

    contents = query.limit(limit + 1).all()
    has_more = len(contents) > limit
    if has_more:
        contents = contents[:limit]

    submissions: list[SubmissionStatusResponse] = []
    for content in contents:
        submission = _build_submission_response(content)
        if submission is not None:
            submissions.append(submission)

    next_cursor = None
    if has_more and contents:
        last_item = contents[-1]
        if last_item.created_at is None:
            raise ValueError("Submission row is missing created_at")
        next_cursor = PaginationCursor.encode_cursor(
            last_id=_require_content_id(last_item.id),
            last_created_at=last_item.created_at,
            filters={},
        )

    return SubmissionStatusListResponse(
        submissions=submissions,
        meta=PaginationMetadata(
            next_cursor=next_cursor,
            has_more=has_more,
            page_size=len(submissions),
            total=len(submissions),
        ),
    )


def _build_submission_response(content: Content) -> SubmissionStatusResponse | None:
    try:
        metadata = metadata_view(content.content_metadata or {})
        raw_content_type = content.content_type
        raw_status = content.status
        if raw_content_type is None or raw_status is None:
            raise ValueError("Submission row is missing required fields")
        detected_feed = _build_detected_feed(metadata.detected_feed())
        feed_subscription = _build_feed_subscription(
            _dict_or_none(metadata.processing_flag("feed_subscription"))
        )
        submission_kind: SubmissionKind = (
            "feed_subscription"
            if _is_feed_subscription_submission(metadata, feed_subscription, detected_feed)
            else "content"
        )
        outcome = _resolve_submission_outcome(
            status=ContentStatus(raw_status),
            submission_kind=submission_kind,
            feed_subscription=feed_subscription,
        )
        return SubmissionStatusResponse(
            id=_require_content_id(content.id),
            content_type=ContentType(raw_content_type),
            url=str(content.url),
            source_url=content.source_url,
            title=content.title,
            status=ContentStatus(raw_status),
            error_message=content.error_message,
            created_at=content.created_at.isoformat() if content.created_at else "",
            processed_at=content.processed_at.isoformat() if content.processed_at else None,
            submitted_via=metadata.processing_flag("submitted_via"),
            is_self_submission=True,
            submission_kind=submission_kind,
            outcome=outcome,
            detected_feed=detected_feed,
            feed_subscription=feed_subscription,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Skipping submission %s due to validation error: %s",
            content.id,
            exc,
            extra={
                "component": "submission_status",
                "operation": "list_submissions",
                "item_id": content.id,
                "context_data": {"content_id": content.id},
            },
        )
        return None


def _require_content_id(content_id: int | None) -> int:
    if content_id is None:
        raise ValueError("Content is missing an id")
    return content_id


def _build_detected_feed(raw_feed: dict[str, Any] | None) -> DetectedFeed | None:
    if not raw_feed:
        return None
    try:
        return DetectedFeed.model_validate(raw_feed)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Ignoring invalid detected feed metadata: %s",
            exc,
            extra={
                "component": "submission_status",
                "operation": "build_detected_feed",
            },
        )
        return None


def _build_feed_subscription(
    raw_subscription: dict[str, Any] | None,
) -> SubmissionFeedSubscriptionResponse | None:
    if not raw_subscription:
        return None

    return SubmissionFeedSubscriptionResponse(
        status=_clean_string(raw_subscription.get("status")) or "unknown",
        feed_url=_clean_string(raw_subscription.get("feed_url")),
        feed_type=_clean_string(raw_subscription.get("feed_type")),
        created=_bool_or_none(raw_subscription.get("created")),
        config_id=_int_or_none(raw_subscription.get("config_id")),
        initial_download=_build_initial_download(
            _dict_or_none(raw_subscription.get("initial_download"))
        ),
    )


def _build_initial_download(
    raw_initial_download: dict[str, Any] | None,
) -> SubmissionFeedInitialDownloadResponse | None:
    if not raw_initial_download:
        return None
    try:
        return SubmissionFeedInitialDownloadResponse.model_validate(raw_initial_download)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Ignoring invalid feed initial download metadata: %s",
            exc,
            extra={
                "component": "submission_status",
                "operation": "build_initial_download",
            },
        )
        return None


def _is_feed_subscription_submission(
    metadata: ContentMetadataView,
    feed_subscription: SubmissionFeedSubscriptionResponse | None,
    detected_feed: DetectedFeed | None,
) -> bool:
    return (
        _is_truthy(metadata.processing_flag("subscribe_to_feed"))
        or feed_subscription is not None
        or detected_feed is not None
    )


def _resolve_submission_outcome(
    *,
    status: ContentStatus,
    submission_kind: SubmissionKind,
    feed_subscription: SubmissionFeedSubscriptionResponse | None,
) -> SubmissionOutcome:
    if submission_kind != "feed_subscription":
        return _content_status_outcome(status)

    if status in {ContentStatus.NEW, ContentStatus.PENDING}:
        return "queued"
    if status == ContentStatus.PROCESSING:
        return "processing"
    if status == ContentStatus.FAILED:
        return "failed"

    subscription_status = (feed_subscription.status if feed_subscription else "").lower()
    if subscription_status == "created":
        return "subscribed"
    if subscription_status == "already_exists":
        return "already_subscribed"
    if subscription_status == "no_feed_found":
        return "feed_not_found"
    if subscription_status == "fetch_failed":
        return "feed_fetch_failed"
    if subscription_status in {
        "missing_user",
        "missing_feed",
        "missing_feed_url",
        "missing_feed_type",
        "unsupported_feed_type",
        "unknown",
    }:
        return "feed_subscription_failed"

    return _content_status_outcome(status)


def _content_status_outcome(status: ContentStatus) -> SubmissionOutcome:
    if status in {ContentStatus.NEW, ContentStatus.PENDING}:
        return "queued"
    if status in {ContentStatus.PROCESSING, ContentStatus.AWAITING_IMAGE}:
        return "processing"
    if status == ContentStatus.COMPLETED:
        return "completed"
    if status == ContentStatus.SKIPPED:
        return "skipped"
    return "failed"


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False
