from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.models.db import BriefingLens, BriefingPendingSource, BriefingSegment
from app.services.briefing.sources import BriefingSource, sources_for_keys
from app.services.news_embeddings import encode_news_texts

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

    changed += _assign_by_centroid(
        db, user_id=user_id, pending_sources=unassigned_news, settings=settings
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
            centroid=_centroid_for_sources(sources),
        )
    elif age_seconds >= 86_400:
        lens = _get_or_create_lens(
            db,
            user_id=user_id,
            key=MISC_LENS_KEY,
            tier="news",
            title="Briefs",
            deck="A mixed desk of fast reads that did not form a larger category yet.",
            position=_next_news_position(db, user_id=user_id),
        )
    else:
        return 0

    for row, _source in unassigned:
        row.lens_key = lens.key
    return len(unassigned)


def _default_lens_name(sources: list[BriefingSource]) -> LensName:
    title_word = "Updates"
    if sources:
        words = [
            word.strip(".,:;!?()[]").title()
            for word in sources[0].title.split()
            if len(word.strip(".,:;!?()[]")) > 3
        ]
        if words:
            title_word = words[0]
    key = "news-" + "".join(ch.lower() if ch.isalnum() else "-" for ch in title_word).strip("-")
    return LensName(
        key=key[:64], title=f"{title_word} desk", deck=f"Fast reads around {title_word.lower()}."
    )


def _next_news_position(db: Session, *, user_id: int) -> int:
    positions = [
        int(row.position or 0)
        for row in db.query(BriefingLens.position).filter(BriefingLens.user_id == user_id).all()
    ]
    return max(positions, default=1) + 1


def _embedding_text(source: BriefingSource) -> str:
    parts = [source.title, source.summary or "", " ".join(source.key_points)]
    return "\n".join(part for part in parts if part)


def _centroid_for_sources(sources: list[BriefingSource]) -> list[float] | None:
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
