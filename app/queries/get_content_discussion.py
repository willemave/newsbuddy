"""Application query for content discussion payloads."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.common import (
    ContentDiscussionResponse,
    DiscussionCommentResponse,
    DiscussionGroupResponse,
    DiscussionItemResponse,
    DiscussionLinkResponse,
)
from app.models.schema import ContentDiscussion
from app.repositories.content_detail_repository import (
    get_content_discussion as get_content_discussion_repository,
)


def _discussion_mode_has_renderable_content(
    *,
    mode: str,
    comments: list[DiscussionCommentResponse],
    discussion_groups: list[DiscussionGroupResponse],
    links: list[DiscussionLinkResponse],
) -> bool:
    if mode == "comments":
        return bool(comments or links)
    if mode == "discussion_list":
        return bool(discussion_groups or links)
    return False


def _infer_discussion_status(
    *,
    mode: str,
    comments: list[DiscussionCommentResponse],
    discussion_groups: list[DiscussionGroupResponse],
    links: list[DiscussionLinkResponse],
    error_message: str | None,
    source_url: str | None,
    discussion_url: str | None,
) -> str:
    if _discussion_mode_has_renderable_content(
        mode=mode,
        comments=comments,
        discussion_groups=discussion_groups,
        links=links,
    ):
        return "completed"
    if error_message:
        return "partial" if source_url or discussion_url else "failed"
    if source_url or discussion_url:
        return "partial"
    return "not_ready"


def build_discussion_response(
    *,
    content_id: int,
    discussion_url: str | None,
    platform: str | None,
    discussion_row: ContentDiscussion | None,
    discussion_data: dict | None = None,
    status: str | None = None,
    error_message: str | None = None,
    fetched_at: str | None = None,
) -> ContentDiscussionResponse:
    if discussion_row is None and discussion_data is None:
        return ContentDiscussionResponse(
            content_id=content_id,
            status="not_ready",
            mode="none",
            platform=platform,
            source_url=discussion_url,
            discussion_url=discussion_url,
            fetched_at=None,
            error_message=None,
            comments=[],
            discussion_groups=[],
            links=[],
            stats={},
        )

    data = discussion_data if isinstance(discussion_data, dict) else None
    if data is None and discussion_row is not None:
        row_data = discussion_row.discussion_data
        data = row_data if isinstance(row_data, dict) else {}
    if data is None:
        data = {}
    mode = (
        data.get("mode") if data.get("mode") in {"none", "comments", "discussion_list"} else "none"
    )

    comments: list[DiscussionCommentResponse] = []
    for entry in data.get("comments", []):
        if not isinstance(entry, dict):
            continue
        comment_id = str(entry.get("comment_id") or "").strip()
        if not comment_id:
            continue
        comments.append(
            DiscussionCommentResponse(
                comment_id=comment_id,
                parent_id=str(entry.get("parent_id")) if entry.get("parent_id") else None,
                author=str(entry.get("author")) if entry.get("author") else None,
                text=str(entry.get("text") or ""),
                compact_text=str(entry.get("compact_text")) if entry.get("compact_text") else None,
                depth=int(entry.get("depth") or 0),
                created_at=str(entry.get("created_at")) if entry.get("created_at") else None,
                source_url=str(entry.get("source_url")) if entry.get("source_url") else None,
            )
        )

    groups: list[DiscussionGroupResponse] = []
    for raw_group in data.get("discussion_groups", []):
        if not isinstance(raw_group, dict):
            continue
        label = str(raw_group.get("label") or "").strip()
        if not label:
            continue

        items: list[DiscussionItemResponse] = []
        for raw_item in raw_group.get("items", []):
            if not isinstance(raw_item, dict):
                continue
            url = str(raw_item.get("url") or "").strip()
            if not url:
                continue
            title = str(raw_item.get("title") or url)
            items.append(DiscussionItemResponse(title=title, url=url))
        groups.append(DiscussionGroupResponse(label=label, items=items))

    links: list[DiscussionLinkResponse] = []
    for raw_link in data.get("links", []):
        if not isinstance(raw_link, dict):
            continue
        url = str(raw_link.get("url") or "").strip()
        if not url:
            continue
        links.append(
            DiscussionLinkResponse(
                url=url,
                source=str(raw_link.get("source") or "unknown"),
                comment_id=str(raw_link.get("comment_id")) if raw_link.get("comment_id") else None,
                group_label=str(raw_link.get("group_label"))
                if raw_link.get("group_label")
                else None,
                title=str(raw_link.get("title")) if raw_link.get("title") else None,
            )
        )

    source_url = str(data.get("source_url")) if data.get("source_url") else discussion_url
    resolved_error_message = (
        discussion_row.error_message
        if discussion_row is not None
        else (str(error_message) if error_message else None)
    )
    resolved_status = (
        discussion_row.status
        if discussion_row is not None
        else (
            status
            or _infer_discussion_status(
                mode=mode,
                comments=comments,
                discussion_groups=groups,
                links=links,
                error_message=resolved_error_message,
                source_url=source_url,
                discussion_url=discussion_url,
            )
        )
    )
    resolved_fetched_at = (
        discussion_row.fetched_at.isoformat()
        if discussion_row and discussion_row.fetched_at
        else fetched_at
    )
    return ContentDiscussionResponse(
        content_id=content_id,
        status=resolved_status,
        mode=mode,
        platform=(discussion_row.platform if discussion_row is not None else None) or platform,
        source_url=source_url,
        discussion_url=discussion_url,
        fetched_at=resolved_fetched_at,
        error_message=resolved_error_message,
        comments=comments,
        discussion_groups=groups,
        links=links,
        stats=data.get("stats") if isinstance(data.get("stats"), dict) else {},
    )


def execute(db: Session, *, user_id: int, content_id: int) -> ContentDiscussionResponse:
    """Return stored discussion payload for a visible content item."""
    content, discussion_row = get_content_discussion_repository(
        db,
        user_id=user_id,
        content_id=content_id,
    )
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
    discussion_url = metadata.get("discussion_url")
    platform = metadata.get("platform") or content.platform

    return build_discussion_response(
        content_id=content_id,
        discussion_url=str(discussion_url) if discussion_url else None,
        platform=str(platform) if platform else None,
        discussion_row=discussion_row,
    )
