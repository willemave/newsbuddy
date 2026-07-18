"""Reconcile X bookmark ledger rows with user-visible Knowledge saves."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.db import (
    Content,
    ContentKnowledgeSave,
    UserIntegrationConnection,
    UserIntegrationSyncedItem,
)
from app.models.metadata.access import metadata_view
from app.services import knowledge as knowledge_service
from app.services.long_form_images import enqueue_visible_long_form_image_if_needed

BOOKMARKS_CHANNEL = "bookmarks"
X_PROVIDER = "x"


@dataclass(frozen=True)
class XBookmarkDestinationResult:
    """Outcome of reconciling one X bookmark into the Knowledge library."""

    user_id: int
    bookmark_content_id: int
    destination_content_id: int | None
    content_exists: bool
    knowledge_save_created: bool
    stale_knowledge_save_removed: bool
    ledger_rows_updated: int

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation for operator tooling."""
        return asdict(self)


@dataclass(frozen=True)
class XBookmarkDestinationPlan:
    """Preview of one persisted X bookmark's destination state."""

    synced_item_id: int
    user_id: int
    bookmark_content_id: int
    destination_content_id: int
    has_destination_save: bool
    has_stale_bookmark_save: bool
    needs_ledger_update: bool

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation for operator tooling."""
        return asdict(self)


def reconcile_x_bookmark_destination(
    db: Session,
    *,
    user_id: int,
    bookmark_content_id: int,
    synced_items: Iterable[UserIntegrationSyncedItem] | None = None,
) -> XBookmarkDestinationResult:
    """Ensure one X bookmark has exactly one canonical Knowledge destination.

    The operation is intentionally idempotent so sync and URL analysis may both
    call it regardless of which worker reaches the content first.
    """
    bookmark_content = db.query(Content).filter(Content.id == bookmark_content_id).first()
    if bookmark_content is None:
        return XBookmarkDestinationResult(
            user_id=user_id,
            bookmark_content_id=bookmark_content_id,
            destination_content_id=None,
            content_exists=False,
            knowledge_save_created=False,
            stale_knowledge_save_removed=False,
            ledger_rows_updated=0,
        )

    destination = _resolve_destination_content(db, bookmark_content)
    destination_id = _require_content_id(destination)
    destination_was_saved = knowledge_service.is_saved_to_knowledge(
        db,
        destination_id,
        user_id,
    )
    if not destination_was_saved:
        knowledge_service.save_to_knowledge(db, destination_id, user_id)

    effective_synced_items = (
        list(synced_items)
        if synced_items is not None
        else _load_user_bookmark_ledger_rows(
            db,
            user_id=user_id,
            content_id=bookmark_content_id,
        )
    )
    ledger_rows_updated = 0
    for synced_item in effective_synced_items:
        if synced_item.content_id == destination_id:
            continue
        synced_item.content_id = destination_id
        ledger_rows_updated += 1

    stale_save_removed = False
    if destination_id != bookmark_content_id and knowledge_service.is_saved_to_knowledge(
        db,
        bookmark_content_id,
        user_id,
    ):
        stale_save_removed = knowledge_service.remove_from_knowledge(
            db,
            bookmark_content_id,
            user_id,
        )
    else:
        db.commit()

    enqueue_visible_long_form_image_if_needed(db, destination)
    return XBookmarkDestinationResult(
        user_id=user_id,
        bookmark_content_id=bookmark_content_id,
        destination_content_id=destination_id,
        content_exists=True,
        knowledge_save_created=not destination_was_saved,
        stale_knowledge_save_removed=stale_save_removed,
        ledger_rows_updated=ledger_rows_updated,
    )


def reconcile_x_bookmark_destinations_for_content(
    db: Session,
    *,
    bookmark_content_id: int,
    fallback_user_id: int | None = None,
) -> list[XBookmarkDestinationResult]:
    """Reconcile every user's ledger row that currently points at one content shell."""
    rows = (
        db.query(UserIntegrationSyncedItem, UserIntegrationConnection.user_id)
        .join(
            UserIntegrationConnection,
            UserIntegrationConnection.id == UserIntegrationSyncedItem.connection_id,
        )
        .filter(UserIntegrationConnection.provider == X_PROVIDER)
        .filter(UserIntegrationSyncedItem.channel == BOOKMARKS_CHANNEL)
        .filter(UserIntegrationSyncedItem.content_id == bookmark_content_id)
        .all()
    )
    synced_items_by_user_id: dict[int, list[UserIntegrationSyncedItem]] = {}
    for synced_item, row_user_id in rows:
        synced_items_by_user_id.setdefault(int(row_user_id), []).append(synced_item)

    if fallback_user_id is not None:
        synced_items_by_user_id.setdefault(fallback_user_id, [])

    results: list[XBookmarkDestinationResult] = []
    for user_id, synced_items in synced_items_by_user_id.items():
        results.append(
            reconcile_x_bookmark_destination(
                db,
                user_id=user_id,
                bookmark_content_id=bookmark_content_id,
                synced_items=synced_items,
            )
        )
    return results


def preview_x_bookmark_destination_reconciliation(
    db: Session,
    *,
    user_id: int | None = None,
    limit: int = 100,
) -> list[XBookmarkDestinationPlan]:
    """Return bounded repair plans for persisted X bookmark ledger rows."""
    bounded_limit = max(1, int(limit))
    query = (
        db.query(
            UserIntegrationSyncedItem,
            UserIntegrationConnection.user_id,
            Content,
            ContentKnowledgeSave.id,
        )
        .join(
            UserIntegrationConnection,
            UserIntegrationConnection.id == UserIntegrationSyncedItem.connection_id,
        )
        .join(Content, Content.id == UserIntegrationSyncedItem.content_id)
        .outerjoin(
            ContentKnowledgeSave,
            and_(
                ContentKnowledgeSave.user_id == UserIntegrationConnection.user_id,
                ContentKnowledgeSave.content_id == UserIntegrationSyncedItem.content_id,
            ),
        )
        .filter(UserIntegrationConnection.provider == X_PROVIDER)
        .filter(UserIntegrationSyncedItem.channel == BOOKMARKS_CHANNEL)
        .filter(
            or_(
                ContentKnowledgeSave.id.is_(None),
                Content.content_metadata["canonical_content_id"].as_string().isnot(None),
                Content.content_metadata["processing"]["canonical_content_id"]
                .as_string()
                .isnot(None),
            )
        )
        .order_by(UserIntegrationSyncedItem.id.asc())
    )
    if user_id is not None:
        query = query.filter(UserIntegrationConnection.user_id == user_id)

    plans: list[XBookmarkDestinationPlan] = []
    for synced_item, row_user_id, bookmark_content, bookmark_save_id in query.yield_per(
        bounded_limit
    ):
        plan = _build_destination_plan(
            db,
            synced_item=synced_item,
            user_id=int(row_user_id),
            bookmark_content=bookmark_content,
            has_bookmark_save=bookmark_save_id is not None,
        )
        if plan is None:
            continue
        plans.append(plan)
        if len(plans) >= bounded_limit:
            break
    return plans


def repair_x_bookmark_destinations(
    db: Session,
    *,
    user_id: int | None = None,
    limit: int = 100,
) -> tuple[list[XBookmarkDestinationPlan], list[XBookmarkDestinationResult]]:
    """Repair a bounded batch of X bookmark Knowledge destinations."""
    plans = preview_x_bookmark_destination_reconciliation(
        db,
        user_id=user_id,
        limit=limit,
    )
    results: list[XBookmarkDestinationResult] = []
    for plan in plans:
        synced_item = (
            db.query(UserIntegrationSyncedItem)
            .filter(UserIntegrationSyncedItem.id == plan.synced_item_id)
            .first()
        )
        if synced_item is None:
            continue
        results.append(
            reconcile_x_bookmark_destination(
                db,
                user_id=plan.user_id,
                bookmark_content_id=plan.bookmark_content_id,
                synced_items=[synced_item],
            )
        )
    return plans, results


def list_x_bookmark_destination_content_ids(
    db: Session,
    *,
    user_id: int,
    content_ids: Iterable[int],
) -> set[int]:
    """Return content IDs backed by this user's X bookmark ledger."""
    normalized_ids = {int(content_id) for content_id in content_ids}
    if not normalized_ids:
        return set()
    rows = (
        db.query(UserIntegrationSyncedItem.content_id)
        .join(
            UserIntegrationConnection,
            UserIntegrationConnection.id == UserIntegrationSyncedItem.connection_id,
        )
        .filter(UserIntegrationConnection.user_id == user_id)
        .filter(UserIntegrationConnection.provider == X_PROVIDER)
        .filter(UserIntegrationSyncedItem.channel == BOOKMARKS_CHANNEL)
        .filter(UserIntegrationSyncedItem.content_id.in_(normalized_ids))
        .distinct()
        .all()
    )
    return {int(content_id) for (content_id,) in rows if content_id is not None}


def _build_destination_plan(
    db: Session,
    *,
    synced_item: UserIntegrationSyncedItem,
    user_id: int,
    bookmark_content: Content,
    has_bookmark_save: bool,
) -> XBookmarkDestinationPlan | None:
    synced_item_id = _require_synced_item_id(synced_item)
    bookmark_content_id = _require_content_id(bookmark_content)
    destination = _resolve_destination_content(db, bookmark_content)
    destination_id = _require_content_id(destination)
    has_destination_save = (
        has_bookmark_save
        if destination_id == bookmark_content_id
        else knowledge_service.is_saved_to_knowledge(db, destination_id, user_id)
    )
    has_stale_bookmark_save = destination_id != bookmark_content_id and has_bookmark_save
    needs_ledger_update = bookmark_content_id != destination_id
    if has_destination_save and not has_stale_bookmark_save and not needs_ledger_update:
        return None

    return XBookmarkDestinationPlan(
        synced_item_id=synced_item_id,
        user_id=user_id,
        bookmark_content_id=bookmark_content_id,
        destination_content_id=destination_id,
        has_destination_save=has_destination_save,
        has_stale_bookmark_save=has_stale_bookmark_save,
        needs_ledger_update=needs_ledger_update,
    )


def _resolve_destination_content(db: Session, content: Content) -> Content:
    current = content
    visited_ids: set[int] = set()
    while True:
        current_id = _require_content_id(current)
        if current_id in visited_ids:
            return current
        visited_ids.add(current_id)

        raw_canonical_id = metadata_view(current.content_metadata).processing_flag(
            "canonical_content_id"
        )
        try:
            canonical_id = int(raw_canonical_id)
        except (TypeError, ValueError):
            return current
        if canonical_id <= 0 or canonical_id in visited_ids:
            return current

        canonical = db.query(Content).filter(Content.id == canonical_id).first()
        if canonical is None:
            return current
        current = canonical


def _load_user_bookmark_ledger_rows(
    db: Session,
    *,
    user_id: int,
    content_id: int,
) -> list[UserIntegrationSyncedItem]:
    return (
        db.query(UserIntegrationSyncedItem)
        .join(
            UserIntegrationConnection,
            UserIntegrationConnection.id == UserIntegrationSyncedItem.connection_id,
        )
        .filter(UserIntegrationConnection.user_id == user_id)
        .filter(UserIntegrationConnection.provider == X_PROVIDER)
        .filter(UserIntegrationSyncedItem.channel == BOOKMARKS_CHANNEL)
        .filter(UserIntegrationSyncedItem.content_id == content_id)
        .all()
    )


def _require_content_id(content: Content) -> int:
    if content.id is None:
        raise ValueError("X bookmark content must be persisted")
    return int(content.id)


def _require_synced_item_id(synced_item: UserIntegrationSyncedItem) -> int:
    if synced_item.id is None:
        raise ValueError("X bookmark ledger row must be persisted")
    return int(synced_item.id)
