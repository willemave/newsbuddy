"""Shared query builders for user-visible content feed endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.constants import CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY
from app.models.contracts import ContentStatus, ContentType
from app.models.schema import Content, ContentFavorites, ContentReadStatus, ContentStatusEntry


@dataclass(frozen=True)
class FeedQueryRows:
    """Common query tuples for feed endpoints."""

    content: Content
    is_read: bool
    is_favorited: bool


def apply_created_at_cursor(query, last_created_at: datetime | None, last_id: int | None):
    """Apply `(created_at, id)` keyset cursor to a query."""
    if not last_created_at or not last_id:
        return query
    return query.filter(
        or_(
            Content.created_at < last_created_at,
            and_(Content.created_at == last_created_at, Content.id < last_id),
        )
    )


def build_user_feed_query(
    db: Session,
    user_id: int,
    *,
    mode: Literal["inbox", "favorites", "recently_read"] = "inbox",
):
    """Build a base query for user content feeds.

    Args:
        db: Active SQLAlchemy session.
        user_id: User identifier.
        mode: Feed mode controlling base joins/filters.

    Returns:
        SQLAlchemy query with joins for read/favorite flags.
    """
    query = (
        db.query(
            Content,
            ContentReadStatus.id.label("is_read"),
            ContentFavorites.id.label("is_favorited"),
        )
        .outerjoin(
            ContentReadStatus,
            and_(
                ContentReadStatus.content_id == Content.id,
                ContentReadStatus.user_id == user_id,
            ),
        )
        .outerjoin(
            ContentFavorites,
            and_(
                ContentFavorites.content_id == Content.id,
                ContentFavorites.user_id == user_id,
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
    elif mode == "favorites":
        query = query.filter(ContentFavorites.id.is_not(None))
    elif mode == "recently_read":
        query = query.filter(ContentReadStatus.id.is_not(None))

    return query
