"""Relation matching and representative enrichment for visible news items."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.contracts import NewsItemStatus, NewsItemVisibilityScope
from app.models.schema import NewsItem
from app.services.news_embeddings import encode_news_texts
from app.utils.url_utils import normalize_http_url

MATCH_TOKEN_PATTERN = re.compile(r"[a-z0-9]{3,}")
MATCH_STOPWORDS = {
    "about",
    "after",
    "against",
    "along",
    "also",
    "amid",
    "been",
    "between",
    "from",
    "have",
    "into",
    "more",
    "news",
    "over",
    "that",
    "their",
    "them",
    "they",
    "this",
    "with",
}
MULTI_VIEW_WEIGHTS = {
    "title": 0.55,
    "content": 0.35,
    "provenance": 0.10,
}
SEMANTIC_PREFILTER_MIN_TITLE_TOKEN_OVERLAP = 1
SEMANTIC_PREFILTER_MAX_CANDIDATES = 12


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _normalize_title_key(value: str | None) -> str | None:
    cleaned = _clean_string(value)
    if not cleaned:
        return None
    return cleaned.casefold()


def _normalize_match_token(token: str) -> str:
    normalized = token.casefold()
    if normalized.endswith("ing") and len(normalized) > 6:
        normalized = normalized[:-3]
    elif (
        (normalized.endswith("ed") and len(normalized) > 5)
        or (normalized.endswith("es") and len(normalized) > 5)
    ):
        normalized = normalized[:-2]
    elif normalized.endswith("s") and len(normalized) > 4:
        normalized = normalized[:-1]
    return normalized


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def matching_text(item: NewsItem) -> str:
    """Build the combined labeled text for evidence ranking and evals."""
    return "\n".join(
        part
        for part in (
            title_matching_text(item),
            provenance_matching_text(item),
            content_matching_text(item),
        )
        if part
    )


def title_matching_text(item: NewsItem) -> str:
    """Return the title-specific view text for one item."""
    title = _clean_string(item.summary_title or item.article_title)
    return f"Title: {title}" if title else ""


def _key_point_texts(item: NewsItem) -> list[str]:
    key_points = item.summary_key_points if isinstance(item.summary_key_points, list) else []
    values: list[str] = []
    for raw in key_points[:5]:
        cleaned = _clean_string(raw if not isinstance(raw, dict) else raw.get("text"))
        if cleaned:
            values.append(cleaned)
    return values


def content_matching_text(item: NewsItem) -> str:
    """Return the summary/content view text for one item."""
    parts: list[str] = []
    key_points = _key_point_texts(item)
    if key_points:
        parts.append("Key points:\n" + "\n".join(f"- {point}" for point in key_points))
    summary_text = _clean_string(item.summary_text)
    if summary_text:
        parts.append(f"Summary: {summary_text}")
    return "\n".join(parts)


def provenance_matching_text(item: NewsItem) -> str:
    """Return the provenance/source view text for one item."""
    parts: list[str] = []
    domain = _clean_string(item.article_domain)
    if domain:
        parts.append(f"Domain: {domain}")
    source = _clean_string(item.source_label)
    if source:
        parts.append(f"Source surface: {source}")
    platform = _clean_string(item.platform)
    if platform:
        parts.append(f"Platform: {platform}")
    return "\n".join(parts)


def match_tokens(item: NewsItem) -> set[str]:
    """Return normalized lexical tokens for one news item."""
    title = _clean_string(item.summary_title or item.article_title)
    return match_tokens_for_text(title or "")


def match_tokens_for_text(text: str) -> set[str]:
    """Return normalized lexical tokens for already-built matching text."""
    tokens: set[str] = set()
    for token in MATCH_TOKEN_PATTERN.findall(text.casefold()):
        normalized = _normalize_match_token(token)
        if normalized and normalized not in MATCH_STOPWORDS:
            tokens.add(normalized)
    return tokens


def relaxed_lexical_guard(
    left: NewsItem,
    right: NewsItem,
    *,
    left_tokens: set[str] | None = None,
    right_tokens: set[str] | None = None,
) -> bool:
    """Allow secondary semantic matches only when source/title cues also align."""
    left_domain = _clean_string(left.article_domain)
    right_domain = _clean_string(right.article_domain)
    if left_domain and right_domain and left_domain.casefold() == right_domain.casefold():
        return True

    left_source = _clean_string(left.source_label)
    right_source = _clean_string(right.source_label)
    if left_source and right_source and left_source.casefold() == right_source.casefold():
        return True

    overlap = (left_tokens or match_tokens(left)) & (right_tokens or match_tokens(right))
    return len(overlap) >= 1


def _normalized_domain(item: NewsItem) -> str | None:
    domain = _clean_string(item.article_domain)
    return domain.casefold() if domain else None


def _normalized_source(item: NewsItem) -> str | None:
    source = _clean_string(item.source_label)
    return source.casefold() if source else None


def _semantic_prefilter_candidates(
    item: NewsItem,
    candidates: list[NewsItem],
) -> tuple[list[NewsItem], dict[int, set[str]], set[str]]:
    """Narrow semantic matching to title-adjacent candidates.

    The runtime worker cannot afford to embed every recent ready representative
    on CPU. Exact URL/id keys are handled before this function runs, so here we
    only keep candidates that share at least one normalized title token with the
    item and then rank that shortlist by lexical overlap plus source/domain cues.
    """
    item_tokens = match_tokens(item)
    if not item_tokens:
        return [], {}, item_tokens

    item_domain = _normalized_domain(item)
    item_source = _normalized_source(item)
    ranked: list[tuple[int, int, int, int, NewsItem, set[str]]] = []
    for index, candidate in enumerate(candidates):
        candidate_tokens = match_tokens(candidate)
        overlap = len(item_tokens & candidate_tokens)
        if overlap < SEMANTIC_PREFILTER_MIN_TITLE_TOKEN_OVERLAP:
            continue

        domain_match = int(item_domain is not None and item_domain == _normalized_domain(candidate))
        source_match = int(item_source is not None and item_source == _normalized_source(candidate))
        ranked.append(
            (
                overlap,
                domain_match,
                source_match,
                -index,
                candidate,
                candidate_tokens,
            )
        )

    if not ranked:
        return [], {}, item_tokens

    ranked.sort(key=lambda entry: entry[:4], reverse=True)
    selected = ranked[:SEMANTIC_PREFILTER_MAX_CANDIDATES]
    selected_candidates = [candidate for *_prefix, candidate, _tokens in selected]
    selected_tokens = {candidate.id: tokens for *_prefix, candidate, tokens in selected}
    return selected_candidates, selected_tokens, item_tokens


def _combine_view_scores(*, view_scores: dict[str, list[float | None]]) -> list[float]:
    combined_scores: list[float] = []
    score_columns = zip(*view_scores.values(), strict=True)
    for scores in score_columns:
        weighted_sum = 0.0
        active_weight = 0.0
        for label, score in zip(view_scores.keys(), scores, strict=True):
            if score is None:
                continue
            weight = MULTI_VIEW_WEIGHTS[label]
            weighted_sum += weight * score
            active_weight += weight
        combined_scores.append((weighted_sum / active_weight) if active_weight else -1.0)
    return combined_scores


def _view_similarity_scores(
    item: NewsItem,
    candidates: list[NewsItem],
    *,
    view_builder: Callable[[NewsItem], str],
) -> list[float | None]:
    item_text = view_builder(item)
    if not item_text:
        return [None] * len(candidates)

    candidate_texts = [view_builder(candidate) for candidate in candidates]
    non_empty_indexes = [index for index, text in enumerate(candidate_texts) if text]
    if not non_empty_indexes:
        return [None] * len(candidates)

    vectors = encode_news_texts(
        [item_text, *[candidate_texts[index] for index in non_empty_indexes]]
    )
    if vectors.size == 0:
        return [None] * len(candidates)

    raw_scores = vectors[0] @ vectors[1:].T
    scores: list[float | None] = [None] * len(candidates)
    for position, candidate_index in enumerate(non_empty_indexes):
        scores[candidate_index] = float(raw_scores[position])
    return scores


def exact_relation_key(item: NewsItem) -> tuple[str, str] | None:
    """Return the strongest exact relation key for one news item."""
    for prefix, candidate in (
        ("story", item.canonical_story_url or item.article_url),
        ("item", item.canonical_item_url or item.discussion_url),
    ):
        normalized = normalize_http_url(candidate) if candidate else None
        if normalized:
            return prefix, normalized

    if item.platform and item.source_external_id:
        return "external", f"{item.platform}:{item.source_external_id}"
    return None


def exact_title_relation_key(item: NewsItem) -> str | None:
    """Return a normalized exact-title key for deduping repeated summaries."""
    return _normalize_title_key(item.summary_title or item.article_title)


def select_best_evidence_item(items: list[NewsItem]) -> NewsItem:
    """Choose the cluster member with the richest display evidence."""

    def _sort_key(item: NewsItem) -> tuple[int, datetime, int]:
        return (
            len(matching_text(item)),
            _coerce_utc(item.ingested_at) or datetime.min,
            item.id,
        )

    return max(items, key=_sort_key)


def list_cluster_members(db: Session, *, representative_id: int) -> list[NewsItem]:
    """Load one representative and its suppressed members."""
    rows = (
        db.query(NewsItem)
        .filter(
            or_(
                NewsItem.id == representative_id,
                NewsItem.representative_news_item_id == representative_id,
            )
        )
        .order_by(NewsItem.ingested_at.asc(), NewsItem.id.asc())
        .all()
    )
    return rows


def _cluster_payload(items: list[NewsItem]) -> dict[str, Any]:
    source_labels: list[str] = []
    domains: list[str] = []
    discussion_snippets: list[str] = []
    related_titles: list[str] = []
    latest_member_ingested_at: datetime | None = None

    for item in items:
        source_label = _clean_string(item.source_label)
        if source_label and source_label not in source_labels:
            source_labels.append(source_label)

        domain = _clean_string(item.article_domain)
        if domain and domain not in domains:
            domains.append(domain)

        title = _clean_string(item.summary_title or item.article_title)
        if title and title not in related_titles:
            related_titles.append(title)

        raw_metadata = dict(item.raw_metadata or {})
        top_comment = raw_metadata.get("top_comment")
        if isinstance(top_comment, dict):
            snippet = _clean_string(top_comment.get("text"))
            if snippet and snippet not in discussion_snippets:
                discussion_snippets.append(snippet)

        item_ingested_at = _coerce_utc(item.ingested_at)
        if item_ingested_at and (
            latest_member_ingested_at is None or item_ingested_at > latest_member_ingested_at
        ):
            latest_member_ingested_at = item_ingested_at

    return {
        "member_ids": [item.id for item in items],
        "source_labels": source_labels,
        "domains": domains,
        "discussion_snippets": discussion_snippets[:5],
        "related_titles": related_titles,
        "latest_member_ingested_at": (
            latest_member_ingested_at.isoformat() if latest_member_ingested_at else None
        ),
    }


def recompute_representative_enrichment(
    db: Session,
    *,
    representative_id: int,
) -> NewsItem:
    """Recompute representative summary fields and cluster metadata."""
    members = list_cluster_members(db, representative_id=representative_id)
    representative = next((item for item in members if item.id == representative_id), None)
    if representative is None:
        raise ValueError(f"Representative news item {representative_id} not found")

    evidence_item = select_best_evidence_item(members)
    cluster_payload = _cluster_payload(members)

    representative.summary_title = evidence_item.summary_title or evidence_item.article_title
    representative.summary_text = evidence_item.summary_text or representative.summary_text
    representative.summary_key_points = list(evidence_item.summary_key_points or [])[:5]
    representative.article_title = evidence_item.article_title or representative.article_title
    representative.article_url = evidence_item.article_url or representative.article_url
    representative.canonical_story_url = (
        evidence_item.canonical_story_url or representative.canonical_story_url
    )
    representative.article_domain = evidence_item.article_domain or representative.article_domain
    representative.cluster_size = len(members)
    representative.enrichment_updated_at = _utcnow_naive()

    representative_metadata = dict(representative.raw_metadata or {})
    representative_metadata["cluster"] = cluster_payload
    representative.raw_metadata = representative_metadata

    for member in members:
        if member.id == representative.id:
            member.representative_news_item_id = None
            member.cluster_size = len(members)
            member.enrichment_updated_at = representative.enrichment_updated_at
            continue
        member.representative_news_item_id = representative.id
        member.cluster_size = len(members)
        member.enrichment_updated_at = representative.enrichment_updated_at

    db.flush()
    return representative


def find_related_representative(
    db: Session,
    *,
    item: NewsItem,
) -> NewsItem | None:
    """Find an existing visible representative that should absorb the given item."""
    settings = get_settings()
    query = (
        db.query(NewsItem)
        .filter(NewsItem.status == NewsItemStatus.READY.value)
        .filter(NewsItem.representative_news_item_id.is_(None))
        .filter(NewsItem.id != item.id)
        .filter(NewsItem.visibility_scope == item.visibility_scope)
    )
    if item.visibility_scope == NewsItemVisibilityScope.USER.value:
        query = query.filter(NewsItem.owner_user_id == item.owner_user_id)
    else:
        query = query.filter(NewsItem.owner_user_id.is_(None))

    lookback_floor = _utcnow_naive() - timedelta(days=settings.news_list_related_lookback_days)
    query = query.filter(NewsItem.ingested_at >= lookback_floor)

    candidates = (
        query.order_by(NewsItem.ingested_at.desc(), NewsItem.id.desc())
        .limit(settings.news_list_max_related_candidates)
        .all()
    )
    if not candidates:
        return None

    exact_key = exact_relation_key(item)
    if exact_key is not None:
        for candidate in candidates:
            if exact_relation_key(candidate) == exact_key:
                return candidate

    exact_title_key = exact_title_relation_key(item)
    if exact_title_key is not None:
        for candidate in candidates:
            if exact_title_relation_key(candidate) == exact_title_key:
                return candidate

    semantic_candidates, candidate_tokens_by_id, item_tokens = _semantic_prefilter_candidates(
        item,
        candidates,
    )
    if not semantic_candidates:
        return None

    similarity_scores = _combine_view_scores(
        view_scores={
            "title": _view_similarity_scores(
                item,
                semantic_candidates,
                view_builder=title_matching_text,
            ),
            "content": _view_similarity_scores(
                item,
                semantic_candidates,
                view_builder=content_matching_text,
            ),
            "provenance": _view_similarity_scores(
                item,
                semantic_candidates,
                view_builder=provenance_matching_text,
            ),
        },
    )
    best_candidate: NewsItem | None = None
    best_score = -1.0
    for index, candidate in enumerate(semantic_candidates):
        score = float(similarity_scores[index])
        if score >= settings.news_list_primary_similarity_threshold and score > best_score:
            best_candidate = candidate
            best_score = score
            continue
        if score < settings.news_list_secondary_similarity_threshold or score <= best_score:
            continue

        candidate_tokens = candidate_tokens_by_id[candidate.id]
        if relaxed_lexical_guard(
            item,
            candidate,
            left_tokens=item_tokens,
            right_tokens=candidate_tokens,
        ):
            best_candidate = candidate
            best_score = score

    return best_candidate


def reconcile_news_item_relation(
    db: Session,
    *,
    news_item_id: int,
) -> NewsItem:
    """Assign the item to a representative cluster and recompute enrichment."""
    item = db.query(NewsItem).filter(NewsItem.id == news_item_id).first()
    if item is None:
        raise ValueError(f"News item {news_item_id} not found")

    representative = find_related_representative(db, item=item)
    if representative is None:
        item.representative_news_item_id = None
        db.flush()
        return recompute_representative_enrichment(db, representative_id=item.id)

    item.representative_news_item_id = representative.id
    db.flush()
    return recompute_representative_enrichment(db, representative_id=representative.id)
