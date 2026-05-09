"""Application query for news-item discussion payloads."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.common import ContentDiscussionResponse, DiscussionLinkResponse
from app.models.schema import ContentDiscussion, NewsItem, NewsItemDiscussion
from app.queries.get_content_discussion import build_discussion_response
from app.services.news_feed import get_visible_news_item


def _datetime_to_iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def _status_for_discussion_row(row: NewsItemDiscussion) -> str:
    if row.summary_status == "completed" and row.summary:
        return "completed"
    if row.last_refresh_status == "failed" and row.summary is None:
        return "failed"
    if row.raw_comments_ref is not None:
        return "partial"
    return "not_ready"


def _links_from_summary(summary: dict[str, object] | None) -> list[DiscussionLinkResponse]:
    if not isinstance(summary, dict):
        return []
    raw_links = summary.get("notable_links")
    if not isinstance(raw_links, list):
        return []

    links: list[DiscussionLinkResponse] = []
    seen: set[str] = set()
    for raw_link in raw_links:
        if not isinstance(raw_link, dict):
            continue
        url = raw_link.get("url")
        if not isinstance(url, str) or not url.strip() or url in seen:
            continue
        seen.add(url)
        title = raw_link.get("title") or raw_link.get("reason")
        source_comment_id = raw_link.get("source_comment_id")
        links.append(
            DiscussionLinkResponse(
                url=url,
                source="summary",
                comment_id=source_comment_id if isinstance(source_comment_id, str) else None,
                title=title if isinstance(title, str) else None,
            )
        )
    return links


def _build_response_from_news_item_discussion(
    *,
    news_item_id: int,
    row: NewsItemDiscussion,
) -> ContentDiscussionResponse:
    summary = row.summary if isinstance(row.summary, dict) else None
    status = _status_for_discussion_row(row)
    return ContentDiscussionResponse(
        content_id=news_item_id,
        status=status,
        mode="comments" if row.discussion_url or row.raw_comments_ref or summary else "none",
        platform=row.platform,
        source_url=row.discussion_url,
        discussion_url=row.discussion_url,
        fetched_at=_datetime_to_iso(row.last_comments_fetched_at),
        error_message=row.last_refresh_error if status == "failed" else None,
        comments=[],
        discussion_groups=[],
        links=_links_from_summary(summary),
        summary=summary,
        comment_count=row.comment_count,
        stats={
            "comment_count": row.comment_count,
            "fetched_comment_count": row.fetched_comment_count,
            "raw_comments_sha256": row.raw_comments_sha256,
            "last_count_checked_at": _datetime_to_iso(row.last_count_checked_at),
            "last_comments_fetched_at": _datetime_to_iso(row.last_comments_fetched_at),
            "next_refresh_after": _datetime_to_iso(row.next_refresh_after),
            "summary_status": row.summary_status,
            "summary_version": row.summary_version,
            "summary_generated_at": _datetime_to_iso(row.summary_generated_at),
        },
    )


def build_response_for_news_item(
    db: Session,
    *,
    item: NewsItem,
) -> ContentDiscussionResponse:
    """Build discussion payload for one visible representative news item."""
    news_item_id = item.id
    if news_item_id is None:
        raise ValueError("News item is missing an id")

    news_item_discussion = (
        db.query(NewsItemDiscussion).filter(NewsItemDiscussion.news_item_id == news_item_id).first()
    )
    if news_item_discussion is not None:
        return _build_response_from_news_item_discussion(
            news_item_id=news_item_id,
            row=news_item_discussion,
        )

    discussion_row = None
    if item.legacy_content_id is not None:
        discussion_row = (
            db.query(ContentDiscussion)
            .filter(ContentDiscussion.content_id == item.legacy_content_id)
            .first()
        )

    raw_metadata = (
        item.raw_metadata if discussion_row is None and isinstance(item.raw_metadata, dict) else {}
    )
    embedded_discussion = raw_metadata.get("discussion_payload")
    if not isinstance(embedded_discussion, dict):
        embedded_discussion = None

    fallback_discussion_url = item.discussion_url or item.canonical_item_url
    return build_discussion_response(
        content_id=news_item_id,
        discussion_url=fallback_discussion_url,
        platform=item.platform,
        discussion_row=discussion_row,
        discussion_data=embedded_discussion,
        status=str(raw_metadata["discussion_status"])
        if raw_metadata.get("discussion_status")
        else None,
        error_message=str(raw_metadata["discussion_error"])
        if raw_metadata.get("discussion_error")
        else None,
        fetched_at=str(raw_metadata["discussion_fetched_at"])
        if raw_metadata.get("discussion_fetched_at")
        else None,
    )


def execute(db: Session, *, user_id: int, news_item_id: int) -> ContentDiscussionResponse:
    """Return discussion payload for one visible representative news item."""
    item = get_visible_news_item(
        db,
        user_id=user_id,
        news_item_id=news_item_id,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="News item not found")
    return build_response_for_news_item(db, item=item)
