"""Canonical publication of completed Learning Deck artifacts."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.db import LearningDeck
from app.services.learning_deck_artifacts import (
    StoredLearningDeckArtifact,
    delete_learning_deck_objects,
)
from app.services.learning_deck_common import coerce_string_list, require_int_value, utcnow

logger = get_logger(__name__)


def commit_learning_deck_artifact_promotion(
    db: Session,
    deck: LearningDeck,
    *,
    artifact: StoredLearningDeckArtifact,
    latest_run_id: int | None = None,
    latest_task_id: int | None = None,
    title: str | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> None:
    """Publish one bundle under a locked deck row and retire the previous bundle."""
    if (latest_run_id is None) == (latest_task_id is None):
        raise ValueError("Exactly one Learning Deck attempt id is required")

    deck_id = require_int_value(deck.id, "Learning Deck id")
    old_keys = coerce_string_list(deck.artifact_object_keys)
    new_keys = coerce_string_list(artifact.artifact_object_keys)

    deck.artifact_storage_prefix = artifact.storage_prefix
    deck.deck_object_key = artifact.deck_object_key
    deck.source_notes_object_key = artifact.source_notes_object_key
    deck.source_notes_html_object_key = artifact.source_notes_html_object_key
    deck.artifact_object_keys = new_keys
    if latest_run_id is not None:
        deck.latest_run_id = latest_run_id
        deck.latest_successful_run_id = latest_run_id
    if latest_task_id is not None:
        deck.latest_task_id = latest_task_id
        deck.latest_successful_task_id = latest_task_id
    if title:
        deck.title = title[:500]
    if source_metadata:
        metadata = dict(deck.source_metadata or {})
        metadata.update(source_metadata)
        deck.source_metadata = metadata
    deck.updated_at = utcnow()
    db.commit()

    new_key_set = set(new_keys)
    stale_keys = [key for key in old_keys if key not in new_key_set]
    if not stale_keys:
        return
    try:
        delete_learning_deck_objects(stale_keys)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to retire superseded Learning Deck artifacts",
            extra={
                "component": "learning_decks",
                "operation": "retire_artifacts",
                "item_id": deck_id,
                "context_data": {"stale_object_count": len(stale_keys)},
            },
        )


__all__ = ["commit_learning_deck_artifact_promotion"]
