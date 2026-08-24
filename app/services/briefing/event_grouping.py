"""Group Briefing news sources into event families before window planning.

A Briefing window should hold a few interesting stories, not a few rows. When
several canonical representatives cover one event from different angles, they
travel together as one event so the composer can fold them into a single
passage instead of repeating the event across windows.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.services.briefing.sources import BriefingSource
from app.services.news_embeddings import encode_texts_with_embedding_model
from app.services.news_relations import match_tokens_for_text

logger = get_logger(__name__)


def _event_text(source: BriefingSource) -> str:
    parts = [source.title, source.summary or "", " ".join(source.key_points)]
    return "\n".join(part for part in parts if part)


def _embed_sources(sources: list[BriefingSource], *, settings: Settings) -> np.ndarray | None:
    try:
        vectors = encode_texts_with_embedding_model(
            [_event_text(source) for source in sources],
            model_spec=settings.briefing_category_embedding_model,
            batch_size=settings.briefing_category_embedding_batch_size,
            timeout_seconds=settings.briefing_category_embedding_timeout_seconds,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Briefing event grouping embedding failed; planning windows by arrival",
            extra={
                "component": "briefing",
                "operation": "group_news_events",
                "context_data": {
                    "source_count": len(sources),
                    "embedding_model": settings.briefing_category_embedding_model,
                },
            },
        )
        return None
    if vectors.ndim != 2 or vectors.shape[0] != len(sources):
        return None
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def group_news_events[WindowItem](
    items: list[WindowItem],
    *,
    source_of: Callable[[WindowItem], BriefingSource],
    settings: Settings | None = None,
) -> list[list[WindowItem]]:
    """Partition items into event families, preserving first-arrival order.

    An item joins the existing event whose centroid is closest when that
    similarity clears ``briefing_news_event_similarity`` and the two share at
    least one distinctive title token. Anything else starts a new event.
    """
    if len(items) < 2:
        return [[item] for item in items]

    settings = settings or get_settings()
    sources = [source_of(item) for item in items]
    vectors = _embed_sources(sources, settings=settings)
    if vectors is None:
        return [[item] for item in items]

    threshold = settings.briefing_news_event_similarity
    token_sets = [match_tokens_for_text(source.title) for source in sources]
    events: list[list[int]] = []
    centroids: list[np.ndarray] = []
    event_tokens: list[set[str]] = []
    for index, vector in enumerate(vectors):
        best_event: int | None = None
        best_score = -1.0
        for event_index, centroid in enumerate(centroids):
            score = float(vector @ centroid)
            if score > best_score:
                best_score = score
                best_event = event_index
        if (
            best_event is not None
            and best_score >= threshold
            and token_sets[index] & event_tokens[best_event]
        ):
            events[best_event].append(index)
            member_vectors = vectors[events[best_event]]
            centroid = member_vectors.mean(axis=0)
            norm = float(np.linalg.norm(centroid)) or 1.0
            centroids[best_event] = centroid / norm
            event_tokens[best_event] |= token_sets[index]
            continue
        events.append([index])
        centroids.append(vector)
        event_tokens.append(set(token_sets[index]))

    grouped = [[items[index] for index in event] for event in events]
    if any(len(event) > 1 for event in grouped):
        logger.info(
            "Grouped Briefing news sources into event families",
            extra={
                "component": "briefing",
                "operation": "group_news_events",
                "context_data": {
                    "source_count": len(items),
                    "event_count": len(grouped),
                    "largest_event": max(len(event) for event in grouped),
                },
            },
        )
    return grouped
