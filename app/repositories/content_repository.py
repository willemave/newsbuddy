"""Repository helpers for content visibility and flags."""

from dataclasses import dataclass

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from app.models.metadata import ContentStatus
from app.models.schema import Content, ContentKnowledgeSave, ContentReadStatus, ContentStatusEntry


@dataclass(frozen=True)
class VisibilityContext:
    """Prebuilt correlated subqueries for content visibility."""

    is_in_inbox: object
    is_read: object
    is_saved_to_knowledge: object


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


def apply_visibility_filters(query, context: VisibilityContext):
    """Apply visibility filters for list/search queries."""
    return query.filter(
        and_(
            Content.status == ContentStatus.COMPLETED.value,
            context.is_in_inbox,
        )
    ).filter((Content.classification != "skip") | (Content.classification.is_(None)))


def apply_read_filter(query, read_filter: str, context: VisibilityContext):
    """Apply read/unread filters using correlated subqueries."""
    if read_filter == "unread":
        return query.filter(~context.is_read)
    if read_filter == "read":
        return query.filter(context.is_read)
    return query


def get_visible_content_query(db: Session, context: VisibilityContext, include_flags: bool = False):
    """Return a base query for visible content."""
    if include_flags:
        query = db.query(
            Content,
            context.is_read.label("is_read"),
            context.is_saved_to_knowledge.label("is_saved_to_knowledge"),
        )
    else:
        query = db.query(Content)
    return apply_visibility_filters(query, context)
