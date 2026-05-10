"""Repository for detailed content queries."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy.orm import Session

from app.models.db import Content, ContentBody, ContentDiscussion
from app.repositories.content_repository import build_visibility_context


def get_content_detail(db: Session, *, user_id: int, content_id: int):
    """Return detail row with read and knowledge-save flags."""
    context = build_visibility_context(user_id)
    is_read_expr = cast(Any, context.is_read).label("is_read")
    is_saved_expr = cast(Any, context.is_saved_to_knowledge).label("is_saved_to_knowledge")
    inbox_expr = cast(Any, context.is_in_inbox)
    return (
        db.query(
            Content,
            is_read_expr,
            is_saved_expr,
            ContentBody.content_id.is_not(None).label("body_available"),
            ContentBody.content_format.label("body_format"),
        )
        .outerjoin(
            ContentBody,
            (ContentBody.content_id == Content.id) & (ContentBody.variant == "source"),
        )
        .filter(
            Content.id == content_id,
            Content.status == "completed",
            inbox_expr,
            (Content.classification != "skip") | (Content.classification.is_(None)),
        )
        .first()
    )


def get_visible_content(db: Session, *, user_id: int, content_id: int):
    """Return one visible content row for the given user."""
    context = build_visibility_context(user_id)
    inbox_expr = cast(Any, context.is_in_inbox)
    return (
        db.query(Content)
        .filter(
            Content.id == content_id,
            Content.status == "completed",
            inbox_expr,
            (Content.classification != "skip") | (Content.classification.is_(None)),
        )
        .first()
    )


def get_content_discussion(db: Session, *, user_id: int, content_id: int):
    """Return visible content and discussion rows for discussion endpoint."""
    content = get_visible_content(db, user_id=user_id, content_id=content_id)
    if not content:
        return None, None
    discussion = (
        db.query(ContentDiscussion).filter(ContentDiscussion.content_id == content_id).first()
    )
    return content, discussion
