"""Relation matching and representative enrichment for visible news items."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.db import NewsItem
from app.repositories.news_relation_repository import (
    list_exact_relation_candidates,
    list_ranked_relation_candidates,
)
from app.services.news_embeddings import encode_news_texts
from app.services.news_reranker import rerank_news_documents
from app.utils.news_titles import (
    get_news_article_title,
    get_news_cluster_related_titles,
    get_news_summary_title,
    resolve_news_display_title,
    set_news_article_title,
    set_news_summary_title,
)
from app.utils.title_utils import clean_title
from app.utils.url_utils import normalize_http_url

logger = get_logger(__name__)

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
CLUSTER_RELATED_TITLE_LIMIT = 6
DOMINANT_RERANK_VARIANT_GAP = 0.35
DOMINANT_RERANK_MIN_TITLE_SIMILARITY = 0.55

# Dotted release numbers ("5.3", "2.5.1") and compact model/quarter tokens
# ("s26", "q2", "17e"). Plain integers, prices, and years are deliberately
# excluded: same-story titles routinely disagree on those.
VERSION_DETAIL_PATTERN = re.compile(r"\b\d+(?:\.\d+)+\b")
MODEL_DETAIL_PATTERN = re.compile(r"\b(?:[a-z]{1,6}\d{1,4}[a-z]{0,2}|\d{1,4}[a-z]{1,3})\b")
# Magnitude/ordinal/multiplier suffixes make digit-letter tokens too noisy to
# compare ("16k jobs" vs "16,000 jobs", "$1b" vs "$1.1b", "4th", "2x faster").
NON_DISTINCTIVE_DIGIT_SUFFIXES = {"k", "m", "b", "bn", "t", "tn", "st", "nd", "rd", "th", "s", "x"}

DecisionTraceSink = Callable[[dict[str, Any]], None]
_decision_trace_sink: DecisionTraceSink | None = None


def set_relation_decision_trace_sink(sink: DecisionTraceSink | None) -> None:
    """Install an optional per-decision trace consumer (used by evals)."""
    global _decision_trace_sink
    _decision_trace_sink = sink


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _normalize_match_token(token: str) -> str:
    normalized = token.casefold()
    if normalized.endswith("ing") and len(normalized) > 6:
        normalized = normalized[:-3]
    elif (normalized.endswith("ed") and len(normalized) > 5) or (
        normalized.endswith("es") and len(normalized) > 5
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


def _require_news_item_id(item: NewsItem) -> int:
    item_id = item.id
    if item_id is None:
        raise ValueError("News item is missing an id")
    return int(item_id)


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


def _relation_primary_title(item: NewsItem) -> str | None:
    cluster_titles = get_news_cluster_related_titles(item.raw_metadata)
    if cluster_titles:
        return cluster_titles[0]
    return get_news_summary_title(item.raw_metadata) or get_news_article_title(item.raw_metadata)


def _cluster_related_titles(item: NewsItem) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()

    def _append_title(value: Any) -> None:
        cleaned = clean_title(value)
        if not cleaned:
            return
        key = cleaned.casefold()
        if key in seen:
            return
        seen.add(key)
        titles.append(cleaned)

    raw_cluster = dict(item.raw_metadata or {}).get("cluster")
    related_titles = raw_cluster.get("related_titles") if isinstance(raw_cluster, dict) else None
    if isinstance(related_titles, list):
        for value in related_titles:
            _append_title(value)
            if len(titles) >= CLUSTER_RELATED_TITLE_LIMIT:
                break
    _append_title(
        get_news_summary_title(item.raw_metadata) or get_news_article_title(item.raw_metadata)
    )

    return titles


def title_matching_text(item: NewsItem) -> str:
    """Return the title-specific view text for one item."""
    title = _relation_primary_title(item)
    return f"Title: {title}" if title else ""


def _candidate_title_similarity_scores(
    item: NewsItem,
    candidates: list[NewsItem],
) -> list[float | None]:
    item_text = title_matching_text(item)
    if not item_text:
        return [None] * len(candidates)

    variant_texts: list[str] = []
    variant_candidate_indexes: list[int] = []
    for candidate_index, candidate in enumerate(candidates):
        for title in _cluster_related_titles(candidate):
            variant_texts.append(f"Title: {title}")
            variant_candidate_indexes.append(candidate_index)

    if not variant_texts:
        return [None] * len(candidates)

    vectors = encode_news_texts([item_text, *variant_texts])
    if vectors.size == 0:
        return [None] * len(candidates)

    raw_scores = vectors[0] @ vectors[1:].T
    scores: list[float | None] = [None] * len(candidates)
    for position, candidate_index in enumerate(variant_candidate_indexes):
        score = float(raw_scores[position])
        best_score = scores[candidate_index]
        if best_score is None or score > best_score:
            scores[candidate_index] = score
    return scores


def reranker_query_text(item: NewsItem) -> str:
    title = _relation_primary_title(item)
    return f"Title: {title}" if title else ""


def reranker_candidate_text(title: str) -> str:
    """Build one candidate document per cluster title variant."""
    return f"Title: {title}"


def _candidate_reranker_scores(
    item: NewsItem,
    candidates: list[NewsItem],
) -> list[float]:
    query_text = reranker_query_text(item)
    if not query_text:
        return [0.0] * len(candidates)

    variant_texts: list[str] = []
    variant_candidate_indexes: list[int] = []
    for candidate_index, candidate in enumerate(candidates):
        for title in _cluster_related_titles(candidate):
            variant_texts.append(reranker_candidate_text(title))
            variant_candidate_indexes.append(candidate_index)
    if not variant_texts:
        return [0.0] * len(candidates)

    title_vectors = encode_news_texts([query_text, *variant_texts])
    if title_vectors.size == 0:
        return [0.0] * len(candidates)
    title_variant_scores = [float(score) for score in title_vectors[0] @ title_vectors[1:].T]

    variant_scores = rerank_news_documents(query=query_text, documents=variant_texts)
    grouped_scores: list[list[tuple[float, float]]] = [[] for _ in candidates]
    for index, rerank_score, title_score in zip(
        variant_candidate_indexes,
        variant_scores,
        title_variant_scores,
        strict=True,
    ):
        grouped_scores[index].append((float(rerank_score), float(title_score)))

    aggregated_scores: list[float] = []
    for scores in grouped_scores:
        if not scores:
            aggregated_scores.append(0.0)
            continue
        ordered = sorted(scores, key=lambda entry: entry[0], reverse=True)
        if len(ordered) >= 2:
            top_score, top_title_score = ordered[0]
            second_score, _second_title_score = ordered[1]
            if (
                top_score - second_score >= DOMINANT_RERANK_VARIANT_GAP
                and top_title_score >= DOMINANT_RERANK_MIN_TITLE_SIMILARITY
            ):
                aggregated_scores.append(top_score)
                continue

        support_count = min(2, len(ordered))
        aggregated_scores.append(
            sum(score for score, _title in ordered[:support_count]) / support_count
        )
    return aggregated_scores


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
    title = _relation_primary_title(item)
    return match_tokens_for_text(title or "")


def candidate_match_tokens(item: NewsItem) -> set[str]:
    """Return lexical tokens for the representative's known title variants."""
    tokens: set[str] = set()
    for title in _cluster_related_titles(item):
        tokens.update(match_tokens_for_text(title))
    return tokens


def match_tokens_for_text(text: str) -> set[str]:
    """Return normalized lexical tokens for already-built matching text."""
    tokens: set[str] = set()
    for token in MATCH_TOKEN_PATTERN.findall(text.casefold()):
        normalized = _normalize_match_token(token)
        if normalized and normalized not in MATCH_STOPWORDS:
            tokens.add(normalized)
    return tokens


def distinctive_detail_tokens(titles: list[str]) -> tuple[set[str], set[str]]:
    """Extract (version, model) detail tokens across a set of title variants."""
    versions: set[str] = set()
    models: set[str] = set()
    for title in titles:
        lowered = title.casefold()
        versions.update(VERSION_DETAIL_PATTERN.findall(lowered))
        for token in MODEL_DETAIL_PATTERN.findall(lowered):
            suffix_match = re.search(r"[a-z]+$", token)
            if (
                suffix_match
                and token[: suffix_match.start()].isdigit()
                and suffix_match.group() in NON_DISTINCTIVE_DIGIT_SUFFIXES
            ):
                continue
            models.add(token)
    return versions, models


def distinctive_details_conflict(left_titles: list[str], right_titles: list[str]) -> bool:
    """True when both sides carry version/model details that disagree.

    Targets same-entity/different-event pairs ("GPT-5.3" vs "GPT-5.4",
    "S26" vs "17e") that embed too close together for thresholds to separate.
    Sides with no distinctive details never conflict.
    """
    left_versions, left_models = distinctive_detail_tokens(left_titles)
    right_versions, right_models = distinctive_detail_tokens(right_titles)
    if left_versions and right_versions and not (left_versions & right_versions):
        return True
    return bool(left_models and right_models and not (left_models & right_models))


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
    *,
    item_tokens: set[str],
) -> tuple[list[NewsItem], dict[int, set[str]]]:
    """Narrow semantic matching to title-adjacent candidates.

    The runtime worker cannot afford to embed every recent ready representative
    on CPU. Exact URL/id keys are handled before this function runs, so here we
    only keep candidates that share at least one normalized title token with the
    item and then rank that shortlist by lexical overlap plus source/domain cues.
    """
    if not item_tokens:
        return [], {}

    item_domain = _normalized_domain(item)
    item_source = _normalized_source(item)
    ranked: list[tuple[int, int, int, int, NewsItem, set[str]]] = []
    for index, candidate in enumerate(candidates):
        candidate_tokens = candidate_match_tokens(candidate)
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
        return [], {}

    ranked.sort(key=lambda entry: entry[:4], reverse=True)
    selected = ranked[:SEMANTIC_PREFILTER_MAX_CANDIDATES]
    selected_candidates = [candidate for *_prefix, candidate, _tokens in selected]
    selected_tokens: dict[int, set[str]] = {}
    for *_prefix, candidate, tokens in selected:
        selected_tokens[_require_news_item_id(candidate)] = tokens
    return selected_candidates, selected_tokens


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
    item_view_builder: Callable[[NewsItem], str],
    candidate_view_builder: Callable[[NewsItem], str] | None = None,
) -> list[float | None]:
    item_text = item_view_builder(item)
    if not item_text:
        return [None] * len(candidates)

    builder = candidate_view_builder or item_view_builder
    candidate_texts = [builder(candidate) for candidate in candidates]
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


def find_exact_related_representatives(
    db: Session,
    *,
    item: NewsItem,
) -> list[NewsItem]:
    """Return ready representatives with the same definitive relation key."""
    exact_key = exact_relation_key(item)
    if exact_key is None:
        return []

    settings = get_settings()
    lookback_floor = _utcnow_naive() - timedelta(days=settings.news_list_related_lookback_days)
    candidates = list_exact_relation_candidates(
        db,
        item=item,
        lookback_floor=lookback_floor,
        exact_key=exact_key,
    )
    return [candidate for candidate in candidates if exact_relation_key(candidate) == exact_key]


def select_best_evidence_item(items: list[NewsItem]) -> NewsItem:
    """Choose the cluster member with the richest display evidence."""

    def _sort_key(item: NewsItem) -> tuple[int, datetime, int]:
        return (
            len(matching_text(item)),
            _coerce_utc(item.ingested_at) or datetime.min,
            _require_news_item_id(item),
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


def _cluster_payload(
    items: list[NewsItem],
    *,
    preferred_title: str | None = None,
) -> dict[str, Any]:
    source_labels: list[str] = []
    domains: list[str] = []
    discussion_snippets: list[str] = []
    related_titles: list[str] = []
    latest_member_ingested_at: datetime | None = None

    if preferred_title:
        cleaned_preferred_title = clean_title(preferred_title)
        if cleaned_preferred_title:
            related_titles.append(cleaned_preferred_title)

    for item in items:
        source_label = _clean_string(item.source_label)
        if source_label and source_label not in source_labels:
            source_labels.append(source_label)

        domain = _clean_string(item.article_domain)
        if domain and domain not in domains:
            domains.append(domain)

        raw_metadata = dict(item.raw_metadata or {})
        prior_cluster = raw_metadata.get("cluster")
        prior_related_titles = (
            prior_cluster.get("related_titles") if isinstance(prior_cluster, dict) else None
        )

        for candidate_title in (
            get_news_summary_title(item.raw_metadata),
            get_news_article_title(item.raw_metadata),
            *(prior_related_titles if isinstance(prior_related_titles, list) else []),
        ):
            title = clean_title(candidate_title)
            if title and title not in related_titles:
                related_titles.append(title)

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
    evidence_display_title = resolve_news_display_title(
        evidence_item.raw_metadata,
        summary_text=evidence_item.summary_text,
        fallback=f"News item {representative_id}",
    )
    cluster_payload = _cluster_payload(members, preferred_title=evidence_display_title)

    representative.summary_text = evidence_item.summary_text or representative.summary_text
    representative.summary_key_points = list(evidence_item.summary_key_points or [])[:5]
    representative.article_url = evidence_item.article_url or representative.article_url
    representative.canonical_story_url = (
        evidence_item.canonical_story_url or representative.canonical_story_url
    )
    representative.article_domain = evidence_item.article_domain or representative.article_domain
    representative.cluster_size = len(members)
    representative.enrichment_updated_at = _utcnow_naive()

    representative_metadata = dict(representative.raw_metadata or {})
    representative_metadata = set_news_summary_title(
        representative_metadata,
        representative.summary_title,
    )
    representative_metadata = set_news_article_title(
        representative_metadata,
        representative.article_title,
    )
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


def _emit_decision_trace(trace: dict[str, Any] | None) -> None:
    if trace is None or _decision_trace_sink is None:
        return
    _decision_trace_sink(trace)


def find_related_representatives(
    db: Session,
    *,
    item: NewsItem,
) -> list[NewsItem]:
    """Find all representatives the item matches, ordered best-first.

    More than one entry means the item bridges clusters that should merge.
    """
    settings = get_settings()
    lookback_floor = _utcnow_naive() - timedelta(days=settings.news_list_related_lookback_days)

    accepted_exact = find_exact_related_representatives(db, item=item)
    if accepted_exact:
        exact_trace: dict[str, Any] | None = None
        if _decision_trace_sink is not None:
            exact_trace = {
                "item_id": _require_news_item_id(item),
                "item_title": _relation_primary_title(item),
                "candidate_count": len(accepted_exact),
                "path": "exact",
                "decisions": [],
                "accepted_ids": [_require_news_item_id(candidate) for candidate in accepted_exact],
            }
        _emit_decision_trace(exact_trace)
        return accepted_exact

    item_tokens = match_tokens(item)
    candidates = list_ranked_relation_candidates(
        db,
        item=item,
        lookback_floor=lookback_floor,
        item_tokens=item_tokens,
        limit=settings.news_list_max_related_candidates,
    )
    trace: dict[str, Any] | None = None
    if _decision_trace_sink is not None:
        trace = {
            "item_id": _require_news_item_id(item),
            "item_title": _relation_primary_title(item),
            "candidate_count": len(candidates),
            "path": "no_candidates",
            "decisions": [],
            "accepted_ids": [],
        }
    if not candidates:
        _emit_decision_trace(trace)
        return []

    semantic_candidates, candidate_tokens_by_id = _semantic_prefilter_candidates(
        item,
        candidates,
        item_tokens=item_tokens,
    )
    if not semantic_candidates:
        if trace is not None:
            trace["path"] = "prefilter_empty"
            _emit_decision_trace(trace)
        return []

    view_scores: dict[str, list[float | None]] = {
        "title": _candidate_title_similarity_scores(item, semantic_candidates),
        "content": _view_similarity_scores(
            item,
            semantic_candidates,
            item_view_builder=content_matching_text,
        ),
        "provenance": _view_similarity_scores(
            item,
            semantic_candidates,
            item_view_builder=provenance_matching_text,
        ),
    }
    similarity_scores = _combine_view_scores(view_scores=view_scores)
    ranked_indexes = sorted(
        range(len(semantic_candidates)),
        key=lambda index: float(similarity_scores[index]),
        reverse=True,
    )
    if settings.news_list_reranker_enabled:
        rerank_indexes = ranked_indexes[: settings.news_list_reranker_max_candidates]
        rerank_candidates = [semantic_candidates[index] for index in rerank_indexes]
        try:
            rerank_scores = _candidate_reranker_scores(item, rerank_candidates)
        except Exception:  # noqa: BLE001
            logger.exception(
                "News reranker failed; falling back to embedding-only clustering",
                extra={
                    "component": "news_relations",
                    "operation": "rerank_candidates",
                    "context_data": {
                        "news_item_id": _require_news_item_id(item),
                        "candidate_count": len(rerank_candidates),
                    },
                },
            )
        else:
            best_rerank_index: int | None = None
            best_rerank_score = -1.0
            for local_index, score in enumerate(rerank_scores):
                numeric_score = float(score)
                if numeric_score <= best_rerank_score:
                    continue
                best_rerank_index = local_index
                best_rerank_score = numeric_score
            accepted_rerank: list[NewsItem] = []
            if (
                best_rerank_index is not None
                and best_rerank_score >= settings.news_list_reranker_similarity_threshold
            ):
                accepted_rerank = [rerank_candidates[best_rerank_index]]
            if trace is not None:
                trace["path"] = "reranker"
                trace["decisions"] = [
                    {
                        "candidate_id": _require_news_item_id(candidate),
                        "candidate_title": _relation_primary_title(candidate),
                        "rerank_score": float(score),
                    }
                    for candidate, score in zip(rerank_candidates, rerank_scores, strict=True)
                ]
                trace["accepted_ids"] = [
                    _require_news_item_id(candidate) for candidate in accepted_rerank
                ]
                _emit_decision_trace(trace)
            return accepted_rerank

    item_title = _relation_primary_title(item)
    item_title_variants = [item_title] if item_title else []
    accepted: list[tuple[float, datetime, int, NewsItem]] = []
    for index, candidate in enumerate(semantic_candidates):
        score = float(similarity_scores[index])
        outcome = "below_secondary"
        if score >= settings.news_list_primary_similarity_threshold:
            outcome = "primary_accepted"
        elif score >= settings.news_list_secondary_similarity_threshold:
            candidate_tokens = candidate_tokens_by_id[_require_news_item_id(candidate)]
            if not relaxed_lexical_guard(
                item,
                candidate,
                left_tokens=item_tokens,
                right_tokens=candidate_tokens,
            ):
                outcome = "secondary_guard_rejected"
            elif distinctive_details_conflict(
                item_title_variants,
                _cluster_related_titles(candidate),
            ):
                outcome = "secondary_detail_veto"
            else:
                outcome = "secondary_accepted"
        if outcome in {"primary_accepted", "secondary_accepted"}:
            accepted.append(
                (
                    score,
                    _coerce_utc(candidate.ingested_at) or datetime.min,
                    _require_news_item_id(candidate),
                    candidate,
                )
            )
        if trace is not None:
            trace["decisions"].append(
                {
                    "candidate_id": _require_news_item_id(candidate),
                    "candidate_title": _relation_primary_title(candidate),
                    "views": {
                        label: (
                            float(view_score) if (view_score := scores[index]) is not None else None
                        )
                        for label, scores in view_scores.items()
                    },
                    "combined": score,
                    "outcome": outcome,
                }
            )

    accepted.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
    ordered = [candidate for _score, _ingested_at, _id, candidate in accepted]
    if trace is not None:
        trace["path"] = "embedding"
        trace["accepted_ids"] = [_require_news_item_id(candidate) for candidate in ordered]
        _emit_decision_trace(trace)
    return ordered


def reconcile_news_item_relation(
    db: Session,
    *,
    news_item_id: int,
) -> NewsItem:
    """Assign the item to a representative cluster and recompute enrichment."""
    item = db.query(NewsItem).filter(NewsItem.id == news_item_id).first()
    if item is None:
        raise ValueError(f"News item {news_item_id} not found")

    accepted = find_related_representatives(db, item=item)
    if not accepted:
        item.representative_news_item_id = None
        db.flush()
        return recompute_representative_enrichment(
            db, representative_id=_require_news_item_id(item)
        )

    representative = accepted[0]
    representative_id = _require_news_item_id(representative)
    representative_titles = _cluster_related_titles(representative)
    representative_exact_key = exact_relation_key(representative)
    merged_ids: list[int] = []
    for other in accepted[1:]:
        # The new item matching both clusters is evidence they cover the same
        # story; fold the smaller match into the best one unless their titles
        # disagree on a distinctive detail.
        exact_match = (
            representative_exact_key is not None
            and exact_relation_key(other) == representative_exact_key
        )
        if not exact_match and distinctive_details_conflict(
            representative_titles,
            _cluster_related_titles(other),
        ):
            continue
        other_id = _require_news_item_id(other)
        for member in list_cluster_members(db, representative_id=other_id):
            member.representative_news_item_id = representative_id
        merged_ids.append(other_id)
    if merged_ids:
        logger.info(
            "Bridge-merged news clusters into representative",
            extra={
                "component": "news_relations",
                "operation": "bridge_merge",
                "context_data": {
                    "news_item_id": news_item_id,
                    "representative_id": representative_id,
                    "merged_representative_ids": merged_ids,
                },
            },
        )

    item.representative_news_item_id = representative_id
    db.flush()
    return recompute_representative_enrichment(db, representative_id=representative_id)
