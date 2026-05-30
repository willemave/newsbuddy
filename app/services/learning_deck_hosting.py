"""Object-storage hosting helpers for Learning Deck artifacts."""

from __future__ import annotations

from app.models.db import LearningDeck
from app.services.learning_deck_artifacts import (
    LearningDeckArtifactError,
    guess_learning_deck_content_type,
    normalize_artifact_relative_path,
    read_learning_deck_object,
)
from app.services.learning_deck_common import LearningDeckError, LearningDeckHostedObject
from app.services.learning_deck_viewer import with_learning_deck_navigation_controls


def read_learning_deck_viewer_object(deck: LearningDeck) -> LearningDeckHostedObject:
    """Read the latest hosted deck HTML for a deck."""
    if not deck.deck_object_key or not deck.latest_successful_run_id:
        raise LearningDeckError("Learning Deck is not ready", status_code=404)
    return LearningDeckHostedObject(
        data=with_learning_deck_navigation_controls(
            read_learning_deck_object(str(deck.deck_object_key))
        ),
        media_type="text/html; charset=utf-8",
    )


def read_learning_deck_source_notes_object(deck: LearningDeck) -> LearningDeckHostedObject:
    """Read the latest hosted rendered source notes for a deck."""
    if not deck.source_notes_html_object_key or not deck.latest_successful_run_id:
        raise LearningDeckError("Learning Deck source notes are not ready", status_code=404)
    return LearningDeckHostedObject(
        data=read_learning_deck_object(str(deck.source_notes_html_object_key)),
        media_type="text/html; charset=utf-8",
    )


def read_learning_deck_asset_object(
    deck: LearningDeck,
    *,
    asset_path: str,
) -> LearningDeckHostedObject:
    """Read one local asset from the latest hosted Learning Deck bundle."""
    if not deck.artifact_storage_prefix or not deck.latest_successful_run_id:
        raise LearningDeckError("Learning Deck is not ready", status_code=404)
    try:
        relative_path = normalize_artifact_relative_path(f"assets/{asset_path}")
    except LearningDeckArtifactError as exc:
        raise LearningDeckError("Learning Deck asset is not available", status_code=404) from exc
    object_key = f"{deck.artifact_storage_prefix}/{relative_path}"
    if object_key not in _artifact_object_keys(deck):
        raise LearningDeckError("Learning Deck asset is not available", status_code=404)
    return LearningDeckHostedObject(
        data=read_learning_deck_object(object_key),
        media_type=guess_learning_deck_content_type(relative_path),
    )


def _artifact_object_keys(deck: LearningDeck) -> list[str]:
    value = deck.artifact_object_keys
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]
