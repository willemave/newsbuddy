from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.models.db import BriefingLens, BriefingPendingSource, BriefingSegment
from app.services.briefing.sources import BriefingSource, sources_for_keys
from app.services.llm_agents import get_basic_agent
from app.services.news_embeddings import encode_news_texts, encode_texts_with_embedding_model
from app.services.prompt_library import render_prompt
from app.services.vendor_usage import record_model_usage

logger = get_logger(__name__)

FIXED_LENSES = (
    ("podcasts", "audio", "Podcasts", "Unheard episodes ready for a focused listen.", 0),
    ("articles", "longform", "Articles", "Long reads and essays waiting in your queue.", 1),
)
MISC_LENS_KEY = "misc"


@dataclass(frozen=True)
class LensName:
    key: str
    title: str
    deck: str


class LensNameOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., min_length=2, max_length=64)
    title: str = Field(..., min_length=2, max_length=40)
    deck: str = Field(..., min_length=8, max_length=180)


@dataclass
class _SemanticCluster:
    rows: list[BriefingPendingSource] = field(default_factory=list)
    sources: list[BriefingSource] = field(default_factory=list)
    vectors: list[list[float]] = field(default_factory=list)
    centroid: list[float] = field(default_factory=list)

    def add(
        self,
        row: BriefingPendingSource,
        source: BriefingSource,
        vector: list[float],
    ) -> None:
        self.rows.append(row)
        self.sources.append(source)
        self.vectors.append(vector)
        self.centroid = _mean_vector(self.vectors)


def build_llm_lens_namer(
    *,
    settings: Settings,
    task_id: int | None,
    user_id: int | None,
) -> Callable[[list[BriefingSource]], LensName]:
    def name_lens(sources: list[BriefingSource]) -> LensName:
        return _name_lens_with_llm(
            sources,
            settings=settings,
            task_id=task_id,
            user_id=user_id,
        )

    return name_lens


def ensure_base_lenses(db: Session, *, user_id: int) -> None:
    for key, tier, title, deck, position in FIXED_LENSES:
        _get_or_create_lens(
            db,
            user_id=user_id,
            key=key,
            tier=tier,
            title=title,
            deck=deck,
            position=position,
        )


def assign_pending_lenses(
    db: Session,
    *,
    user_id: int,
    naming_fn: Callable[[list[BriefingSource]], LensName] | None = None,
    settings: Settings | None = None,
) -> int:
    """Assign unbucketed pending news sources to active or newly-created news lenses."""

    settings = settings or get_settings()
    pending = (
        db.query(BriefingPendingSource)
        .filter(BriefingPendingSource.user_id == user_id)
        .filter(BriefingPendingSource.lens_key.is_(None))
        .order_by(BriefingPendingSource.enqueued_at.asc(), BriefingPendingSource.id.asc())
        .all()
    )
    if not pending:
        return 0

    source_map = sources_for_keys(
        db,
        user_id=user_id,
        source_keys=[f"{row.source_kind}:{row.source_id}" for row in pending],
    )
    changed = 0
    unassigned_news: list[tuple[BriefingPendingSource, BriefingSource]] = []
    for row in pending:
        source = source_map.get(f"{row.source_kind}:{row.source_id}")
        if source is None:
            db.delete(row)
            changed += 1
            continue
        if source.kind == "content" and source.lens_key:
            row.lens_key = source.lens_key
            changed += 1
            continue
        if source.kind == "news" and source.topic_slug:
            lens_key = f"news-{source.topic_slug}"
            _get_or_create_lens(
                db,
                user_id=user_id,
                key=lens_key,
                tier="news",
                title=source.topic_title or source.topic_slug.replace("-", " ").title(),
                deck=f"Fast reads around {source.topic_title or source.topic_slug}.",
                position=_next_news_position(db, user_id=user_id),
            )
            row.lens_key = lens_key
            changed += 1
        elif source.kind == "news":
            unassigned_news.append((row, source))

    remaining = [(row, source) for row, source in unassigned_news if row.lens_key is None]
    if settings.briefing_semantic_category_assignment_enabled and naming_fn is not None:
        changed += _assign_by_semantic_categories(
            db,
            user_id=user_id,
            pending_sources=remaining,
            naming_fn=naming_fn,
            settings=settings,
        )
        remaining = [(row, source) for row, source in unassigned_news if row.lens_key is None]
        changed += _assign_stale_misc_lens(
            db,
            user_id=user_id,
            pending_sources=remaining,
            settings=settings,
        )
    else:
        changed += _assign_stale_misc_lens(
            db,
            user_id=user_id,
            pending_sources=remaining,
            settings=settings,
        )
        remaining = [(row, source) for row, source in unassigned_news if row.lens_key is None]
        changed += _assign_by_centroid(
            db,
            user_id=user_id,
            pending_sources=remaining,
            settings=settings,
        )
        remaining = [(row, source) for row, source in unassigned_news if row.lens_key is None]
        changed += _assign_new_or_misc_lens(
            db,
            user_id=user_id,
            pending_sources=remaining,
            naming_fn=naming_fn,
            settings=settings,
        )
    return changed


def retire_idle_lenses(
    db: Session,
    *,
    user_id: int,
    idle_days: int,
) -> int:
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=idle_days)
    active_lenses = (
        db.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id)
        .filter(BriefingLens.status == "active")
        .filter(~BriefingLens.key.in_(["podcasts", "articles"]))
        .all()
    )
    if not active_lenses:
        return 0
    lens_ids_with_segments = {
        row[0]
        for row in db.query(BriefingSegment.lens_id)
        .filter(BriefingSegment.lens_id.in_([lens.id for lens in active_lenses]))
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .distinct()
        .all()
    }
    lens_keys_with_pending = {
        row[0]
        for row in db.query(BriefingPendingSource.lens_key)
        .filter(BriefingPendingSource.user_id == user_id)
        .filter(BriefingPendingSource.lens_key.in_([lens.key for lens in active_lenses]))
        .distinct()
        .all()
    }
    retired = 0
    for lens in active_lenses:
        if lens.id in lens_ids_with_segments or lens.key in lens_keys_with_pending:
            continue
        if lens.updated_at and lens.updated_at > cutoff:
            continue
        lens.status = "retired"
        lens.retired_at = datetime.now(UTC).replace(tzinfo=None)
        retired += 1
    return retired


def _get_or_create_lens(
    db: Session,
    *,
    user_id: int,
    key: str,
    tier: str,
    title: str,
    deck: str,
    position: int,
    centroid: list[float] | None = None,
) -> BriefingLens:
    lens = (
        db.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id, BriefingLens.key == key)
        .first()
    )
    if lens is not None:
        if lens.status != "active":
            lens.status = "active"
            lens.retired_at = None
        return lens
    lens = BriefingLens(
        user_id=user_id,
        key=key,
        tier=tier,
        title=title,
        deck=deck,
        position=position,
        status="active",
        centroid=centroid,
    )
    db.add(lens)
    db.flush()
    return lens


def _assign_by_centroid(
    db: Session,
    *,
    user_id: int,
    pending_sources: list[tuple[BriefingPendingSource, BriefingSource]],
    settings: Settings,
) -> int:
    if not settings.briefing_centroid_assignment_enabled:
        return 0
    sources = [(row, source) for row, source in pending_sources if row.lens_key is None]
    if not sources:
        return 0
    lenses = (
        db.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id)
        .filter(BriefingLens.tier == "news")
        .filter(BriefingLens.status == "active")
        .all()
    )
    centroid_lenses = [lens for lens in lenses if isinstance(lens.centroid, list)]
    if not centroid_lenses:
        return 0

    try:
        vectors = encode_news_texts([_embedding_text(source) for _row, source in sources])
    except Exception:
        logger.exception(
            "Briefing centroid assignment embedding failed; leaving sources unassigned",
            extra={"user_id": user_id, "source_count": len(sources)},
        )
        return 0
    if vectors.size == 0:
        return 0

    changed = 0
    for index, (row, _source) in enumerate(sources):
        vector = [float(value) for value in vectors[index].tolist()]
        best_lens = None
        best_score = -1.0
        for lens in centroid_lenses:
            centroid = lens.centroid
            if not isinstance(centroid, list):
                continue
            score = _cosine(vector, [float(value) for value in centroid])
            if score > best_score:
                best_score = score
                best_lens = lens
        if best_lens is None or best_score < settings.briefing_category_similarity:
            continue
        row.lens_key = best_lens.key
        best_centroid = best_lens.centroid
        if not isinstance(best_centroid, list):
            continue
        best_lens.centroid = _running_mean([float(value) for value in best_centroid], vector)
        changed += 1
    return changed


def _assign_by_semantic_categories(
    db: Session,
    *,
    user_id: int,
    pending_sources: list[tuple[BriefingPendingSource, BriefingSource]],
    naming_fn: Callable[[list[BriefingSource]], LensName],
    settings: Settings,
) -> int:
    sources = [(row, source) for row, source in pending_sources if row.lens_key is None]
    if not sources:
        return 0

    lenses = (
        db.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id)
        .filter(BriefingLens.tier == "news")
        .filter(BriefingLens.status == "active")
        .filter(BriefingLens.key != MISC_LENS_KEY)
        .order_by(BriefingLens.position.asc(), BriefingLens.id.asc())
        .all()
    )
    texts = [_embedding_text(source) for _row, source in sources]
    texts.extend(_lens_profile_text(lens) for lens in lenses)
    try:
        vectors = encode_texts_with_embedding_model(
            texts,
            model_spec=settings.briefing_category_embedding_model,
            batch_size=settings.briefing_category_embedding_batch_size,
            timeout_seconds=settings.briefing_category_embedding_timeout_seconds,
        )
    except Exception:
        logger.exception(
            "Briefing semantic category embedding failed",
            extra={
                "component": "briefing",
                "operation": "assign_semantic_categories",
                "item_id": user_id,
                "context_data": {
                    "source_count": len(sources),
                    "lens_count": len(lenses),
                    "embedding_model": settings.briefing_category_embedding_model,
                },
            },
        )
        raise
    if vectors.size == 0:
        return 0

    source_vectors = [
        [float(value) for value in vectors[index].tolist()] for index in range(len(sources))
    ]
    lens_profile_vectors = [
        [float(value) for value in vectors[len(sources) + index].tolist()]
        for index in range(len(lenses))
    ]
    vector_size = len(source_vectors[0]) if source_vectors else 0
    lens_vectors = _resolve_lens_vectors(
        lenses,
        lens_profile_vectors=lens_profile_vectors,
        vector_size=vector_size,
    )

    changed = _assign_sources_to_existing_lenses(
        sources,
        source_vectors=source_vectors,
        lens_vectors=lens_vectors,
        similarity_threshold=settings.briefing_category_similarity,
    )
    remaining = [
        (row, source, vector)
        for (row, source), vector in zip(sources, source_vectors, strict=True)
        if row.lens_key is None
    ]
    if not remaining:
        return changed

    clusters = _cluster_sources_by_embedding(
        remaining,
        similarity_threshold=settings.briefing_category_cluster_similarity,
    )
    ready_clusters, small_clusters = _split_ready_clusters(
        clusters,
        min_items=settings.briefing_new_lens_min_items,
    )
    small_source_count = sum(len(cluster.sources) for cluster in small_clusters)
    if small_source_count >= settings.briefing_new_lens_min_items:
        ready_clusters.extend(
            _pack_small_clusters(
                small_clusters,
                min_items=settings.briefing_new_lens_min_items,
                max_items=settings.briefing_news_window_max,
            )
        )

    names = _name_clusters_or_defaults(
        ready_clusters,
        naming_fn=naming_fn,
        user_id=user_id,
        max_workers=settings.briefing_compose_parallelism,
        news_window_max=settings.briefing_news_window_max,
    )
    for cluster, raw_name in zip(ready_clusters, names, strict=True):
        name = _unique_lens_name(
            db,
            user_id=user_id,
            name=raw_name,
        )
        lens = _get_or_create_lens(
            db,
            user_id=user_id,
            key=name.key,
            tier="news",
            title=name.title,
            deck=name.deck,
            position=_next_news_position(db, user_id=user_id),
            centroid=cluster.centroid,
        )
        for row in cluster.rows:
            row.lens_key = lens.key
            changed += 1
    return changed


def _name_clusters_or_defaults(
    clusters: list[_SemanticCluster],
    *,
    naming_fn: Callable[[list[BriefingSource]], LensName],
    user_id: int,
    max_workers: int,
    news_window_max: int,
) -> list[LensName]:
    if not clusters:
        return []
    workers = min(max(max_workers, 1), len(clusters))
    if workers <= 1:
        return [
            _name_cluster_or_default(
                cluster.sources[:news_window_max],
                naming_fn=naming_fn,
                user_id=user_id,
            )
            for cluster in clusters
        ]

    names: list[LensName | None] = [None] * len(clusters)

    def name_one(index: int, cluster: _SemanticCluster) -> tuple[int, LensName]:
        return (
            index,
            _name_cluster_or_default(
                cluster.sources[:news_window_max],
                naming_fn=naming_fn,
                user_id=user_id,
            ),
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(name_one, index, cluster): index
            for index, cluster in enumerate(clusters)
        }
        for future in as_completed(futures):
            index, name = future.result()
            names[index] = name

    return [
        name if name is not None else _default_lens_name(cluster.sources[:news_window_max])
        for name, cluster in zip(names, clusters, strict=True)
    ]


def _name_cluster_or_default(
    sources: list[BriefingSource],
    *,
    naming_fn: Callable[[list[BriefingSource]], LensName],
    user_id: int,
) -> LensName:
    try:
        return naming_fn(sources)
    except Exception:
        logger.exception(
            "Briefing lens naming failed; using fallback lens name",
            extra={
                "component": "briefing",
                "operation": "name_semantic_cluster",
                "item_id": user_id,
                "context_data": {"source_count": len(sources)},
            },
        )
        return _default_lens_name(sources)


def _split_ready_clusters(
    clusters: list[_SemanticCluster],
    *,
    min_items: int,
) -> tuple[list[_SemanticCluster], list[_SemanticCluster]]:
    ready: list[_SemanticCluster] = []
    small: list[_SemanticCluster] = []
    for cluster in clusters:
        if len(cluster.sources) >= min_items:
            ready.append(cluster)
        else:
            small.append(cluster)
    return ready, small


def _merge_clusters(clusters: list[_SemanticCluster]) -> _SemanticCluster:
    merged = _SemanticCluster()
    for cluster in clusters:
        for row, source, vector in zip(cluster.rows, cluster.sources, cluster.vectors, strict=True):
            merged.add(row, source, vector)
    return merged


def _pack_small_clusters(
    clusters: list[_SemanticCluster],
    *,
    min_items: int,
    max_items: int,
) -> list[_SemanticCluster]:
    remaining = list(clusters)
    packed: list[_SemanticCluster] = []
    while remaining:
        seed_index = _most_connected_cluster_index(remaining)
        group = remaining.pop(seed_index)
        while remaining and len(group.sources) < max_items:
            candidate_index = _nearest_cluster_index(
                group,
                remaining,
                max_items=max_items,
            )
            if candidate_index is None:
                break
            group = _merge_clusters([group, remaining.pop(candidate_index)])
        if len(group.sources) < min_items and packed:
            target_index = _nearest_cluster_index(group, packed, max_items=max_items)
            if target_index is None:
                target_index = _nearest_cluster_index(group, packed, max_items=None)
            if target_index is not None:
                packed[target_index] = _merge_clusters([packed[target_index], group])
                continue
        packed.append(group)
    return [cluster for cluster in packed if len(cluster.sources) >= min_items]


def _most_connected_cluster_index(clusters: list[_SemanticCluster]) -> int:
    if len(clusters) == 1:
        return 0
    best_index = 0
    best_score = -1.0
    for index, cluster in enumerate(clusters):
        scores = [
            _cosine(cluster.centroid, other.centroid)
            for other_index, other in enumerate(clusters)
            if other_index != index
        ]
        score = sum(scores) / len(scores) if scores else -1.0
        if score > best_score:
            best_index = index
            best_score = score
    return best_index


def _nearest_cluster_index(
    base: _SemanticCluster,
    candidates: list[_SemanticCluster],
    *,
    max_items: int | None,
) -> int | None:
    best_index = None
    best_score = -1.0
    for index, candidate in enumerate(candidates):
        if max_items is not None and len(base.sources) + len(candidate.sources) > max_items:
            continue
        score = _cosine(base.centroid, candidate.centroid)
        if score > best_score:
            best_index = index
            best_score = score
    return best_index


def _assign_sources_to_existing_lenses(
    sources: list[tuple[BriefingPendingSource, BriefingSource]],
    *,
    source_vectors: list[list[float]],
    lens_vectors: list[tuple[BriefingLens, list[float]]],
    similarity_threshold: float,
) -> int:
    if not lens_vectors:
        return 0
    changed = 0
    for (row, _source), vector in zip(sources, source_vectors, strict=True):
        best_lens = None
        best_score = -1.0
        for lens, lens_vector in lens_vectors:
            score = _cosine(vector, lens_vector)
            if score > best_score:
                best_score = score
                best_lens = lens
        if best_lens is None or best_score < similarity_threshold:
            continue
        row.lens_key = best_lens.key
        current = best_lens.centroid
        best_lens.centroid = (
            _running_mean([float(value) for value in current], vector)
            if isinstance(current, list) and len(current) == len(vector)
            else vector
        )
        changed += 1
    return changed


def _cluster_sources_by_embedding(
    sources: list[tuple[BriefingPendingSource, BriefingSource, list[float]]],
    *,
    similarity_threshold: float,
) -> list[_SemanticCluster]:
    clusters: list[_SemanticCluster] = []
    for row, source, vector in sources:
        best_cluster = None
        best_score = -1.0
        for cluster in clusters:
            score = _cosine(vector, cluster.centroid)
            if score > best_score:
                best_score = score
                best_cluster = cluster
        if best_cluster is not None and best_score >= similarity_threshold:
            best_cluster.add(row, source, vector)
            continue
        cluster = _SemanticCluster()
        cluster.add(row, source, vector)
        clusters.append(cluster)
    return clusters


def _resolve_lens_vectors(
    lenses: list[BriefingLens],
    *,
    lens_profile_vectors: list[list[float]],
    vector_size: int,
) -> list[tuple[BriefingLens, list[float]]]:
    lens_vectors: list[tuple[BriefingLens, list[float]]] = []
    for lens, profile_vector in zip(lenses, lens_profile_vectors, strict=True):
        centroid = lens.centroid
        if isinstance(centroid, list) and len(centroid) == vector_size:
            lens_vectors.append((lens, [float(value) for value in centroid]))
            continue
        lens_vectors.append((lens, profile_vector))
    return lens_vectors


def _assign_stale_misc_lens(
    db: Session,
    *,
    user_id: int,
    pending_sources: list[tuple[BriefingPendingSource, BriefingSource]],
    settings: Settings,
) -> int:
    unassigned = [(row, source) for row, source in pending_sources if row.lens_key is None]
    if not unassigned or len(unassigned) >= settings.briefing_new_lens_min_items:
        return 0
    now = datetime.now(UTC).replace(tzinfo=None)
    oldest = min(
        (row.enqueued_at for row, _source in unassigned if row.enqueued_at is not None),
        default=now,
    )
    if (now - oldest).total_seconds() < 86_400:
        return 0

    lens = _get_or_create_misc_lens(db, user_id=user_id)
    for row, _source in unassigned:
        row.lens_key = lens.key
    return len(unassigned)


def _assign_new_or_misc_lens(
    db: Session,
    *,
    user_id: int,
    pending_sources: list[tuple[BriefingPendingSource, BriefingSource]],
    naming_fn: Callable[[list[BriefingSource]], LensName] | None,
    settings: Settings,
) -> int:
    unassigned = [(row, source) for row, source in pending_sources if row.lens_key is None]
    if not unassigned:
        return 0
    now = datetime.now(UTC).replace(tzinfo=None)
    oldest = min(
        (row.enqueued_at for row, _source in unassigned if row.enqueued_at is not None),
        default=now,
    )
    age_seconds = (now - oldest).total_seconds()
    should_make_new = len(unassigned) >= settings.briefing_new_lens_min_items
    if should_make_new:
        sources = [source for _row, source in unassigned[: settings.briefing_news_window_max]]
        name = naming_fn(sources) if naming_fn else _default_lens_name(sources)
        lens = _get_or_create_lens(
            db,
            user_id=user_id,
            key=name.key,
            tier="news",
            title=name.title,
            deck=name.deck,
            position=_next_news_position(db, user_id=user_id),
            centroid=_centroid_for_sources(sources, settings=settings),
        )
    elif age_seconds >= 86_400:
        lens = _get_or_create_misc_lens(db, user_id=user_id)
    else:
        return 0

    for row, _source in unassigned:
        row.lens_key = lens.key
    return len(unassigned)


def _get_or_create_misc_lens(db: Session, *, user_id: int) -> BriefingLens:
    return _get_or_create_lens(
        db,
        user_id=user_id,
        key=MISC_LENS_KEY,
        tier="news",
        title="Briefs",
        deck="A mixed desk of fast reads that did not form a larger category yet.",
        position=_next_news_position(db, user_id=user_id),
    )


def _default_lens_name(sources: list[BriefingSource]) -> LensName:
    title_word = "Updates"
    if sources:
        words = [
            word.strip(".,:;!?()[]")
            for word in sources[0].title.split()
            if len(word.strip(".,:;!?()[]")) > 3
        ]
        if words:
            title_word = words[0]
    key = "news-" + "".join(ch.lower() if ch.isalnum() else "-" for ch in title_word).strip("-")
    return LensName(
        key=key[:64], title=f"{title_word} desk", deck=f"Fast reads around {title_word.lower()}."
    )


def _name_lens_with_llm(
    sources: list[BriefingSource],
    *,
    settings: Settings,
    task_id: int | None,
    user_id: int | None,
) -> LensName:
    started_at = time.perf_counter()
    model_spec = settings.briefing_model
    system_prompt = render_prompt("briefing/lens_naming#system")
    user_prompt = render_prompt(
        "briefing/lens_naming#user",
        source_payload_json=json.dumps(
            [_source_payload(source) for source in sources],
            ensure_ascii=False,
            indent=2,
        ),
    )
    agent = get_basic_agent(model_spec, LensNameOutput, system_prompt)
    result = agent.run_sync(
        user_prompt,
        model_settings={"timeout": settings.briefing_llm_timeout_seconds},
    )
    record_model_usage(
        "briefing_lens_naming",
        result,
        model_spec=model_spec,
        persist={
            "feature": "briefing_lens_naming",
            "operation": "briefing.name_lens",
            "source": "queue" if task_id else "api",
            "task_id": task_id,
            "user_id": user_id,
            "metadata": {
                "source_count": len(sources),
                "generation_ms": round((time.perf_counter() - started_at) * 1000),
            },
        },
    )
    return LensName(
        key=result.output.key,
        title=result.output.title,
        deck=result.output.deck,
    )


def _source_payload(source: BriefingSource) -> dict[str, object]:
    return {
        "source_key": source.source_key,
        "title": source.title,
        "summary": source.summary,
        "key_points": source.key_points,
        "url": source.url,
        "published_at": source.published_at.isoformat() if source.published_at else None,
    }


def _unique_lens_name(db: Session, *, user_id: int, name: LensName) -> LensName:
    existing_keys = {
        str(row[0])
        for row in db.query(BriefingLens.key).filter(BriefingLens.user_id == user_id).all()
    }
    base_key = _normalize_lens_key(name.key or name.title)
    key = base_key
    suffix = 2
    while key in existing_keys:
        suffix_text = f"-{suffix}"
        key = f"{base_key[: 64 - len(suffix_text)].rstrip('-')}{suffix_text}"
        suffix += 1
    return LensName(
        key=key,
        title=" ".join(name.title.split()).strip()[:40],
        deck=" ".join(name.deck.split()).strip(),
    )


def _normalize_lens_key(value: str) -> str:
    slug = value.strip().lower()
    slug = slug.removeprefix("news-")
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    if not slug:
        slug = "updates"
    return f"news-{slug}"[:64].rstrip("-")


def _next_news_position(db: Session, *, user_id: int) -> int:
    positions = [
        int(row.position or 0)
        for row in db.query(BriefingLens.position).filter(BriefingLens.user_id == user_id).all()
    ]
    return max(positions, default=1) + 1


def _embedding_text(source: BriefingSource) -> str:
    parts = [source.title, source.summary or "", " ".join(source.key_points)]
    return "\n".join(part for part in parts if part)


def _lens_profile_text(lens: BriefingLens) -> str:
    return "\n".join(part for part in [str(lens.title or ""), str(lens.deck or "")] if part)


def _centroid_for_sources(
    sources: list[BriefingSource],
    *,
    settings: Settings,
) -> list[float] | None:
    if not settings.briefing_centroid_assignment_enabled:
        return None
    if not sources:
        return None
    try:
        vectors = encode_news_texts([_embedding_text(source) for source in sources])
    except Exception:
        logger.exception(
            "Briefing lens centroid embedding failed; creating lens without centroid",
            extra={"source_count": len(sources)},
        )
        return None
    if vectors.size == 0:
        return None
    return [float(value) for value in vectors.mean(axis=0).tolist()]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return -1.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return numerator / (left_norm * right_norm)


def _running_mean(current: list[float], vector: list[float]) -> list[float]:
    if len(current) != len(vector):
        return vector
    return [(a + b) / 2.0 for a, b in zip(current, vector, strict=True)]


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    width = len(vectors[0])
    if width == 0:
        return []
    totals = [0.0] * width
    count = 0
    for vector in vectors:
        if len(vector) != width:
            continue
        count += 1
        for index, value in enumerate(vector):
            totals[index] += value
    if count == 0:
        return []
    return [value / count for value in totals]
