"""Repository for detailed content queries."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.db import Content, ContentBody, ContentDiscussion
from app.repositories.content_repository import VisibilityContext, build_visibility_context


def _is_visible_to_user(context: VisibilityContext) -> ColumnElement[bool]:
    """Allow inbox content and explicit Knowledge saves to open."""
    return or_(
        context.is_saved_to_knowledge,
        and_(
            context.is_in_inbox,
            (Content.classification != "skip") | (Content.classification.is_(None)),
        ),
    )


def get_content_detail(db: Session, *, user_id: int, content_id: int):
    """Return detail row with read and knowledge-save flags."""
    context = build_visibility_context(user_id)
    is_read_expr = cast(Any, context.is_read).label("is_read")
    is_saved_expr = cast(Any, context.is_saved_to_knowledge).label("is_saved_to_knowledge")
    visibility_expr = _is_visible_to_user(context)
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
            visibility_expr,
        )
        .first()
    )


def get_visible_content(db: Session, *, user_id: int, content_id: int):
    """Return one visible content row for the given user."""
    context = build_visibility_context(user_id)
    visibility_expr = _is_visible_to_user(context)
    return (
        db.query(Content)
        .filter(
            Content.id == content_id,
            Content.status == "completed",
            visibility_expr,
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
