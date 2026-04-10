"""Shared query builders for user-visible content feed endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.constants import CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY
from app.models.contracts import ContentStatus, ContentType
from app.models.schema import Content, ContentKnowledgeSave, ContentReadStatus, ContentStatusEntry


@dataclass(frozen=True)
class FeedQueryRows:
    """Common query tuples for feed endpoints."""

    content: Content
    is_read: bool
    is_saved_to_knowledge: bool


def content_sort_timestamp_expr():
    """Return the feed timestamp used for list ordering and grouping."""
    return func.coalesce(Content.publication_date, Content.processed_at, Content.created_at)


def resolve_content_sort_timestamp(content: Content) -> datetime | None:
    """Return the in-Python sort timestamp matching ``content_sort_timestamp_expr``."""
    return content.publication_date or content.processed_at or content.created_at


def apply_sort_timestamp_cursor(
    query,
    last_sort_timestamp: datetime | None,
    last_id: int | None,
    *,
    sort_expr=None,
):
    """Apply `(sort_timestamp, id)` keyset cursor to a query."""
    if not last_sort_timestamp or not last_id:
        return query
    if sort_expr is None:
        sort_expr = Content.created_at
    return query.filter(
        or_(
            sort_expr < last_sort_timestamp,
            and_(sort_expr == last_sort_timestamp, Content.id < last_id),
        )
    )


def build_user_feed_query(
    db: Session,
    user_id: int,
    *,
    mode: Literal["inbox", "knowledge_library", "recently_read"] = "inbox",
):
    """Build a base query for user content feeds.

    Args:
        db: Active SQLAlchemy session.
        user_id: User identifier.
        mode: Feed mode controlling base joins/filters.

    Returns:
        SQLAlchemy query with joins for read and knowledge-save flags.
    """
    query = (
        db.query(
            Content,
            ContentReadStatus.id.label("is_read"),
            ContentKnowledgeSave.id.label("is_saved_to_knowledge"),
        )
        .outerjoin(
            ContentReadStatus,
            and_(
                ContentReadStatus.content_id == Content.id,
                ContentReadStatus.user_id == user_id,
            ),
        )
        .outerjoin(
            ContentKnowledgeSave,
            and_(
                ContentKnowledgeSave.content_id == Content.id,
                ContentKnowledgeSave.user_id == user_id,
            ),
        )
        .filter(Content.status == ContentStatus.COMPLETED.value)
        .filter((Content.classification != "skip") | (Content.classification.is_(None)))
    )
    digest_visibility = Content.content_metadata["digest_visibility"].as_string()
    query = query.filter(
        or_(
            digest_visibility.is_(None),
            digest_visibility != CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY,
        )
    )

    if mode == "inbox":
        query = query.outerjoin(
            ContentStatusEntry,
            and_(
                ContentStatusEntry.content_id == Content.id,
                ContentStatusEntry.user_id == user_id,
                ContentStatusEntry.status == "inbox",
            ),
        )
        query = query.filter(
            or_(
                Content.content_type == ContentType.NEWS.value,
                ContentStatusEntry.id.is_not(None),
            )
        )
    elif mode == "knowledge_library":
        query = query.filter(ContentKnowledgeSave.id.is_not(None))
    elif mode == "recently_read":
        query = query.filter(ContentReadStatus.id.is_not(None))

    return query
