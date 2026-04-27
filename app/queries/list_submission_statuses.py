"""List processing status for the current user's submitted content."""

from __future__ import annotations

from sqlalchemy import String, and_, cast, or_
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.api.common import SubmissionStatusListResponse, SubmissionStatusResponse
from app.models.metadata import ContentStatus, ContentType
from app.models.metadata_access import metadata_view
from app.models.pagination import PaginationMetadata
from app.models.schema import Content
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
        last_id = cursor_data["last_id"]
        last_created_at = cursor_data["last_created_at"]

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

    submissions = [_build_submission_response(content) for content in contents]
    submissions = [submission for submission in submissions if submission is not None]

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
