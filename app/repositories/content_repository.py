"""Repository helpers for content visibility and flags."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.sql.elements import ColumnElement

from app.models.contracts import ContentStatus
from app.models.db import Content, ContentKnowledgeSave, ContentReadStatus, ContentStatusEntry


@dataclass(frozen=True)
class VisibilityContext:
    """Prebuilt correlated subqueries for content visibility."""

    is_in_inbox: ColumnElement[bool]
    is_read: ColumnElement[bool]
    is_saved_to_knowledge: ColumnElement[bool]


def build_visibility_context(user_id: int) -> VisibilityContext:
    """Create correlated subqueries for visibility and per-user flags."""
    is_in_inbox = exists(
        select(ContentStatusEntry.id).where(
            ContentStatusEntry.user_id == user_id,
            ContentStatusEntry.status == "inbox",
            ContentStatusEntry.content_id == Content.id,
        )
    )
    is_read = exists(
        select(ContentReadStatus.id).where(
            ContentReadStatus.user_id == user_id,
            ContentReadStatus.content_id == Content.id,
        )
    )
    is_saved_to_knowledge = exists(
        select(ContentKnowledgeSave.id).where(
            ContentKnowledgeSave.user_id == user_id,
            ContentKnowledgeSave.content_id == Content.id,
        )
    )
    return VisibilityContext(
        is_in_inbox=is_in_inbox,
        is_read=is_read,
        is_saved_to_knowledge=is_saved_to_knowledge,
    )


def apply_visibility_filters(query: Any, context: VisibilityContext) -> Any:
    """Apply visibility filters for list/search queries."""
    return query.filter(
        and_(
            Content.status == ContentStatus.COMPLETED.value,
            context.is_in_inbox,
        )
    ).filter((Content.classification != "skip") | (Content.classification.is_(None)))
