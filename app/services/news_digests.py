"""News-native digest selection, clustering, and persistence."""

from __future__ import annotations

import base64
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, aliased

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.contracts import NewsItemStatus, NewsItemVisibilityScope, TaskStatus, TaskType
from app.models.news_digest_models import (
    NewsDigestBatchBulletDraft,
    NewsDigestBatchDraft,
    NewsDigestBulletDraft,
    NewsDigestHeaderDraft,
)
from app.models.pagination import PaginationMetadata
from app.models.schema import (
    NewsDigest,
    NewsDigestBullet,
    NewsDigestBulletSource,
    NewsItem,
    NewsItemDigestCoverage,
    ProcessingTask,
)
from app.models.user import User
from app.services.llm_agents import get_basic_agent
from app.services.news_digest_preferences import resolve_user_news_digest_preference_prompt
from app.services.news_embeddings import encode_news_texts
from app.utils.url_utils import normalize_http_url

logger = get_logger(__name__)

PIPELINE_VERSION = "news-native-v2-batched"
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
    "digest",
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
HEADER_SYSTEM_PROMPT = (
    "You write the title and short summary for a news digest run. Stay grounded in the bullets "
    "provided. Keep the title punchy and the summary compact."
)
CLUSTER_FALLBACK_SYSTEM_PROMPT = (
    "You write one short-form news digest bullet from a pre-grouped set of evidence items. "
    "Stay strictly grounded in the provided items. Return a concise topic, 2-4 sentences of "
    "details, and the supporting item ids. Do not invent facts or ids."
)
MAX_CLUSTER_KEY_POINTS = 6
MAX_CLUSTER_ITEM_KEY_POINTS = 3
MAX_CLUSTER_SUMMARY_CHARS = 420
MAX_DISCUSSION_COMMENTS = 2


@dataclass(frozen=True)
class NewsDigestCluster:
    """Grouped evidence items destined for one digest bullet."""

    items: list[NewsItem]


@dataclass(frozen=True)
class NewsDigestTriggerDecision:
    """Whether a digest should be generated for a user right now."""

    should_generate: bool
    trigger_reason: str | None
    candidate_count: int
    provisional_group_count: int
    flush_required: bool


@dataclass(frozen=True)
class NewsDigestGenerationResult:
    """Outcome for one digest generation attempt."""

    digest_id: int | None
    source_count: int
    group_count: int
    trigger_reason: str | None
    skipped: bool = False


@dataclass(frozen=True)
class NewsDigestCuratedBulletDraft:
    """Validated bullet draft tied back to one underlying cluster."""

    cluster: NewsDigestCluster
    draft: NewsDigestBulletDraft


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _truncate_text(value: str | None, *, max_chars: int) -> str | None:
    cleaned = _clean_string(value)
    if cleaned is None:
        return None
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[: max_chars - 1].rstrip()}…"


def normalize_timezone(timezone_name: str | None) -> str:
    """Validate and normalize a timezone string."""
    candidate = (timezone_name or "UTC").strip() or "UTC"
    try:
        ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        return "UTC"
    return candidate


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _matching_text(item: NewsItem) -> str:
    parts: list[str] = []
    title = item.summary_title or item.article_title
    for candidate in (
        title,
        item.article_domain,
    ):
        cleaned = _clean_string(candidate)
        if cleaned:
            parts.append(cleaned)

    key_points = item.summary_key_points if isinstance(item.summary_key_points, list) else []
    for raw in key_points[:5]:
        cleaned = _clean_string(raw if not isinstance(raw, dict) else raw.get("text"))
        if cleaned:
            parts.append(cleaned)

    summary_text = _clean_string(item.summary_text)
    if summary_text:
        parts.append(summary_text)

    return "\n".join(parts)


def _match_tokens(item: NewsItem) -> set[str]:
    text = _matching_text(item).casefold()
    return {token for token in MATCH_TOKEN_PATTERN.findall(text) if token not in MATCH_STOPWORDS}


def _lexical_guard(left: NewsItem, right: NewsItem) -> bool:
    if left.article_domain and right.article_domain and left.article_domain == right.article_domain:
        return True
    if left.source_label and right.source_label and left.source_label == right.source_label:
        return True
    overlap = _match_tokens(left) & _match_tokens(right)
    return len(overlap) >= 2


def _exact_group_key(item: NewsItem) -> tuple[str, str]:
    for prefix, candidate in (
        ("story", item.canonical_story_url or item.article_url),
        ("item", item.canonical_item_url or item.discussion_url),
    ):
        normalized = normalize_http_url(candidate) if candidate else None
        if normalized:
            return prefix, normalized

    if item.platform and item.source_external_id:
        return "external", f"{item.platform}:{item.source_external_id}"
    return "id", str(item.id)


def _select_representative(items: list[NewsItem]) -> NewsItem:
    def _sort_key(item: NewsItem) -> tuple[int, datetime, int]:
        return (
            len(_matching_text(item)),
            _coerce_utc(item.ingested_at) or datetime.min,
            item.id,
        )

    return max(items, key=_sort_key)


def _cluster_exact_groups(items: list[NewsItem]) -> list[list[NewsItem]]:
    grouped: dict[tuple[str, str], list[NewsItem]] = {}
    for item in items:
        grouped.setdefault(_exact_group_key(item), []).append(item)
    return list(grouped.values())


def _cluster_semantic(exact_groups: list[list[NewsItem]]) -> list[NewsDigestCluster]:
    if not exact_groups:
        return []

    settings = get_settings()
    representatives = [_select_representative(group) for group in exact_groups]
    parent = list(range(len(exact_groups)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    vectors = encode_news_texts([_matching_text(item) for item in representatives])
    if vectors.size:
        similarity = np.matmul(vectors, vectors.T)
        for left in range(len(representatives)):
            for right in range(left + 1, len(representatives)):
                score = float(similarity[left][right])
                if score >= settings.news_digest_primary_similarity_threshold:
                    union(left, right)
                    continue
                if score >= settings.news_digest_secondary_similarity_threshold and _lexical_guard(
                    representatives[left], representatives[right]
                ):
                    union(left, right)

    grouped_clusters: dict[int, list[NewsItem]] = {}
    for index, exact_group in enumerate(exact_groups):
        grouped_clusters.setdefault(find(index), []).extend(exact_group)

    clusters = [
        NewsDigestCluster(items=cluster_items) for cluster_items in grouped_clusters.values()
    ]
    return sorted(
        clusters,
        key=lambda cluster: (
            len(cluster.items),
            max((_coerce_utc(item.ingested_at) or datetime.min) for item in cluster.items),
        ),
        reverse=True,
    )


def cluster_news_items(items: list[NewsItem]) -> list[NewsDigestCluster]:
    """Group news items using exact dedupe followed by semantic similarity."""
    exact_groups = _cluster_exact_groups(items)
    return _cluster_semantic(exact_groups)


def list_visible_uncovered_news_items(
    db: Session,
    *,
    user_id: int,
    limit: int | None = None,
) -> list[NewsItem]:
    """Return visible news items that have not yet been covered for a user."""
    settings = get_settings()
    coverage = aliased(NewsItemDigestCoverage)
    query = (
        db.query(NewsItem)
        .outerjoin(
            coverage,
            and_(
                coverage.user_id == user_id,
                coverage.news_item_id == NewsItem.id,
            ),
        )
        .filter(coverage.id.is_(None))
        .filter(NewsItem.status == NewsItemStatus.READY.value)
        .filter(
            or_(
                NewsItem.visibility_scope == NewsItemVisibilityScope.GLOBAL.value,
                and_(
                    NewsItem.visibility_scope == NewsItemVisibilityScope.USER.value,
                    NewsItem.owner_user_id == user_id,
                ),
            )
        )
        .order_by(NewsItem.ingested_at.asc(), NewsItem.id.asc())
    )
    query_limit = limit or settings.news_digest_max_candidates
    return query.limit(query_limit).all()


def get_visible_news_item(db: Session, *, user_id: int, news_item_id: int) -> NewsItem | None:
    """Return a visible news item for a user or ``None`` when inaccessible."""
    return (
        db.query(NewsItem)
        .filter(NewsItem.id == news_item_id)
        .filter(
            or_(
                NewsItem.visibility_scope == NewsItemVisibilityScope.GLOBAL.value,
                and_(
                    NewsItem.visibility_scope == NewsItemVisibilityScope.USER.value,
                    NewsItem.owner_user_id == user_id,
                ),
            )
        )
        .first()
    )


def _has_day_rollover_flush(
    items: list[NewsItem],
    *,
    timezone_name: str,
    now_utc: datetime,
) -> bool:
    timezone = ZoneInfo(normalize_timezone(timezone_name))
    local_today = now_utc.replace(tzinfo=UTC).astimezone(timezone).date()
    for item in items:
        ingested_at = _coerce_utc(item.ingested_at)
        if ingested_at is None:
            continue
        local_date = ingested_at.replace(tzinfo=UTC).astimezone(timezone).date()
        if local_date < local_today:
            return True
    return False


def get_news_digest_trigger_decision(
    db: Session,
    *,
    user: User,
    now_utc: datetime | None = None,
) -> NewsDigestTriggerDecision:
    """Decide whether the scheduler should create a new digest run."""
    settings = get_settings()
    resolved_now = _coerce_utc(now_utc) or _utcnow_naive()
    candidates = list_visible_uncovered_news_items(db, user_id=user.id)
    if not candidates:
        return NewsDigestTriggerDecision(
            should_generate=False,
            trigger_reason=None,
            candidate_count=0,
            provisional_group_count=0,
            flush_required=False,
        )

    flush_required = _has_day_rollover_flush(
        candidates,
        timezone_name=user.news_digest_timezone,
        now_utc=resolved_now,
    )
    clusters = cluster_news_items(candidates)
    last_digest = (
        db.query(NewsDigest)
        .filter(NewsDigest.user_id == user.id)
        .order_by(NewsDigest.generated_at.desc(), NewsDigest.id.desc())
        .first()
    )
    min_interval_elapsed = True
    if last_digest and last_digest.generated_at:
        last_generated = _coerce_utc(last_digest.generated_at) or resolved_now
        elapsed_seconds = (resolved_now - last_generated).total_seconds()
        min_interval_elapsed = elapsed_seconds >= settings.news_digest_min_interval_minutes * 60

    trigger_reason: str | None = None
    if flush_required:
        trigger_reason = "day_rollover_flush"
    elif len(candidates) >= settings.news_digest_min_uncovered_items:
        trigger_reason = "uncovered_item_threshold"
    elif len(clusters) >= settings.news_digest_min_provisional_groups:
        trigger_reason = "provisional_group_threshold"

    should_generate = trigger_reason is not None and (min_interval_elapsed or flush_required)
    return NewsDigestTriggerDecision(
        should_generate=should_generate,
        trigger_reason=trigger_reason,
        candidate_count=len(candidates),
        provisional_group_count=len(clusters),
        flush_required=flush_required,
    )


def _resolve_digest_item_title(item: NewsItem) -> str:
    return item.summary_title or item.article_title or f"News item {item.id}"


def _resolve_outward_url(item: NewsItem) -> str | None:
    for candidate in (
        item.discussion_url,
        item.canonical_item_url,
        item.article_url,
        item.canonical_story_url,
    ):
        normalized = normalize_http_url(candidate) if candidate else None
        if normalized:
            return normalized
    return None


def _coerce_key_points(item: NewsItem, *, limit: int) -> list[str]:
    key_points = item.summary_key_points if isinstance(item.summary_key_points, list) else []
    cleaned_points: list[str] = []
    for raw in key_points[:limit]:
        cleaned = _clean_string(raw if not isinstance(raw, dict) else raw.get("text"))
        if cleaned and cleaned not in cleaned_points:
            cleaned_points.append(cleaned)
    return cleaned_points


def _resolve_cluster_key_points(cluster: NewsDigestCluster) -> list[str]:
    key_points: list[str] = []
    for item in cluster.items:
        for point in _coerce_key_points(item, limit=MAX_CLUSTER_ITEM_KEY_POINTS):
            if point not in key_points:
                key_points.append(point)
            if len(key_points) >= MAX_CLUSTER_KEY_POINTS:
                return key_points
    return key_points


def _resolve_cluster_summary_snippet(cluster: NewsDigestCluster) -> str | None:
    representative = _select_representative(cluster.items)
    candidate_items = [representative] + [
        item for item in cluster.items if item.id != representative.id
    ]
    best_snippet: str | None = None
    for item in candidate_items:
        snippet = _truncate_text(item.summary_text, max_chars=MAX_CLUSTER_SUMMARY_CHARS)
        if snippet is None:
            continue
        if best_snippet is None or len(snippet) > len(best_snippet):
            best_snippet = snippet
        if item.id == representative.id:
            return snippet
    return best_snippet


def _resolve_cluster_discussion_comments(cluster: NewsDigestCluster) -> list[str]:
    comments: list[str] = []
    for item in cluster.items:
        discussion = item.raw_metadata.get("discussion_payload")
        if not isinstance(discussion, dict):
            continue
        compact_comments = discussion.get("compact_comments")
        if not isinstance(compact_comments, list):
            continue
        for raw in compact_comments:
            if not isinstance(raw, str):
                continue
            cleaned = _truncate_text(raw, max_chars=180)
            if cleaned and cleaned not in comments:
                comments.append(cleaned)
            if len(comments) >= MAX_DISCUSSION_COMMENTS:
                return comments
    return comments


def _resolve_cluster_source_labels(cluster: NewsDigestCluster) -> list[str]:
    labels: list[str] = []
    for item in cluster.items:
        cleaned = _clean_string(item.source_label)
        if cleaned and cleaned not in labels:
            labels.append(cleaned)
    return labels


def _resolve_cluster_domains(cluster: NewsDigestCluster) -> list[str]:
    domains: list[str] = []
    for item in cluster.items:
        cleaned = _clean_string(item.article_domain)
        if cleaned and cleaned not in domains:
            domains.append(cleaned)
    return domains


def _cluster_latest_ingested_at(cluster: NewsDigestCluster) -> datetime | None:
    timestamps = [_coerce_utc(item.ingested_at) for item in cluster.items]
    resolved = [timestamp for timestamp in timestamps if timestamp is not None]
    if not resolved:
        return None
    return max(resolved)


def _build_cluster_payload(cluster: NewsDigestCluster, *, rank: int) -> dict[str, Any]:
    representative = _select_representative(cluster.items)
    sorted_items = sorted(cluster.items, key=lambda row: (row.ingested_at or datetime.min, row.id))
    representative_title = _resolve_digest_item_title(representative)
    latest_ingested_at = _cluster_latest_ingested_at(cluster)

    item_payloads: list[dict[str, Any]] = []
    for item in sorted_items:
        item_payloads.append(
            {
                "news_item_id": item.id,
                "title": _resolve_digest_item_title(item),
                "source_label": _clean_string(item.source_label),
                "article_domain": _clean_string(item.article_domain),
                "article_url": _resolve_outward_url(item),
                "summary_text": _truncate_text(item.summary_text, max_chars=220),
                "key_points": _coerce_key_points(item, limit=MAX_CLUSTER_ITEM_KEY_POINTS),
            }
        )

    return {
        "cluster_rank": rank,
        "source_count": len(cluster.items),
        "latest_ingested_at": latest_ingested_at.isoformat() if latest_ingested_at else None,
        "representative_title": representative_title,
        "representative_source_label": _clean_string(representative.source_label),
        "source_labels": _resolve_cluster_source_labels(cluster),
        "domains": _resolve_cluster_domains(cluster),
        "news_item_ids": [item.id for item in sorted_items],
        "summary_text": _resolve_cluster_summary_snippet(cluster),
        "key_points": _resolve_cluster_key_points(cluster),
        "discussion_comments": _resolve_cluster_discussion_comments(cluster),
        "items": item_payloads,
    }


def _build_group_system_prompt(user_preference_prompt: str) -> str:
    return "\n".join(
        [
            (
                "You curate a short-form news digest from a ranked set of pre-grouped "
                "candidate clusters."
            ),
            "Stay strictly grounded in the provided clusters and item ids.",
            (
                "Use the user's digest preference prompt below as the highest-priority "
                "curation layer."
            ),
            "Do not follow it to change writing style; use it only to decide what to include.",
            "",
            "User digest preference prompt:",
            user_preference_prompt.strip(),
            "",
            "Rules:",
            "- Curate, do not exhaustively summarize every cluster.",
            "- Prefer high-signal, concrete, and consequential stories.",
            (
                "- Exclude low-signal junk, blocked galleries, thin Reddit posts, spammy "
                "vendor pages, unverified noise, vague reactions, and repetitive hype "
                "unless the user prompt clearly wants them."
            ),
            "- Each returned bullet must correspond to exactly one input cluster.",
            "- Do not merge multiple clusters into one bullet.",
            "- Return bullets ordered by importance for this user.",
            (
                "- For each bullet, include the source cluster_rank and a non-empty subset "
                "of that cluster's news_item_ids."
            ),
            "- Do not invent facts or ids.",
        ]
    )


def _build_cluster_prompt(cluster: NewsDigestCluster) -> str:
    lines = [
        "Write one digest bullet from these already-grouped items.",
        (
            "The details should synthesize the common story and note meaningful "
            "differences when relevant."
        ),
        "Support the bullet with the cited item ids.",
        "",
        "Items:",
    ]
    for item in sorted(cluster.items, key=lambda row: (row.ingested_at or datetime.min, row.id)):
        lines.append(f"[{item.id}] {_resolve_digest_item_title(item)}")
        if item.source_label:
            lines.append(f"Source label: {item.source_label}")
        if item.article_domain:
            lines.append(f"Article domain: {item.article_domain}")
        if item.article_url:
            lines.append(f"Article URL: {item.article_url}")
        key_points = item.summary_key_points if isinstance(item.summary_key_points, list) else []
        cleaned_points = [
            _clean_string(raw if not isinstance(raw, dict) else raw.get("text"))
            for raw in key_points[:4]
        ]
        cleaned_points = [point for point in cleaned_points if point]
        if cleaned_points:
            lines.append("Key points:")
            lines.extend(f"- {point}" for point in cleaned_points)
        if item.summary_text:
            lines.append(f"Summary text: {item.summary_text}")
        discussion = item.raw_metadata.get("discussion_payload")
        if isinstance(discussion, dict):
            comments = discussion.get("compact_comments")
            if isinstance(comments, list):
                cleaned_comments = [
                    _clean_string(comment) for comment in comments[:2] if isinstance(comment, str)
                ]
                cleaned_comments = [comment for comment in cleaned_comments if comment]
                if cleaned_comments:
                    lines.append("Discussion:")
                    lines.extend(f"- {comment}" for comment in cleaned_comments)
        lines.append("")
    return "\n".join(lines).strip()


def _build_batch_curation_prompt(clusters: list[NewsDigestCluster]) -> str:
    payload = {
        "instruction": (
            "Curate the strongest digest bullets from these ranked candidate clusters. "
            "You may omit clusters that are low-signal or not worth surfacing."
        ),
        "cluster_count": len(clusters),
        "clusters": [
            _build_cluster_payload(cluster, rank=rank)
            for rank, cluster in enumerate(clusters, start=1)
        ],
    }
    return json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)


def _fallback_bullet_draft(cluster: NewsDigestCluster) -> NewsDigestBulletDraft:
    representative = _select_representative(cluster.items)
    key_points = (
        representative.summary_key_points
        if isinstance(representative.summary_key_points, list)
        else []
    )
    details_parts = [
        _clean_string(representative.summary_text),
        _clean_string(key_points[0] if key_points else None),
    ]
    details = next((part for part in details_parts if part), None)
    if details is None:
        details = f"{_resolve_digest_item_title(representative)} is driving this cluster."
    return NewsDigestBulletDraft(
        topic=_resolve_digest_item_title(representative),
        details=details,
        news_item_ids=[item.id for item in cluster.items],
    )


def _generate_bullet_draft(cluster: NewsDigestCluster) -> NewsDigestBulletDraft:
    settings = get_settings()
    try:
        agent = get_basic_agent(
            settings.news_group_model,
            NewsDigestBulletDraft,
            CLUSTER_FALLBACK_SYSTEM_PROMPT,
        )
        result = agent.run_sync(
            _build_cluster_prompt(cluster),
            model_settings={"timeout": settings.worker_timeout_seconds},
        )
        draft = result.output
        valid_ids = {item.id for item in cluster.items}
        selected_ids = [
            news_item_id for news_item_id in draft.news_item_ids if news_item_id in valid_ids
        ]
        if not selected_ids:
            selected_ids = [item.id for item in cluster.items]
        return NewsDigestBulletDraft(
            topic=draft.topic,
            details=draft.details,
            news_item_ids=selected_ids,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("News digest bullet generation failed")
        raise RuntimeError("News digest bullet generation failed") from exc


def _validate_batch_bullet_draft(
    *,
    cluster: NewsDigestCluster,
    draft: NewsDigestBatchBulletDraft,
) -> NewsDigestBulletDraft:
    valid_ids = {item.id for item in cluster.items}
    selected_ids = [
        news_item_id for news_item_id in draft.news_item_ids if news_item_id in valid_ids
    ]
    if not selected_ids:
        selected_ids = [item.id for item in cluster.items]
    return NewsDigestBulletDraft(
        topic=draft.topic,
        details=draft.details,
        news_item_ids=selected_ids,
    )


def _generate_curated_cluster_bullets(
    *,
    user: User,
    clusters: list[NewsDigestCluster],
) -> tuple[list[NewsDigestCuratedBulletDraft], bool]:
    settings = get_settings()
    cluster_by_rank = {
        rank: cluster for rank, cluster in enumerate(clusters, start=1)
    }
    system_prompt = _build_group_system_prompt(
        resolve_user_news_digest_preference_prompt(user)
    )
    try:
        agent = get_basic_agent(
            settings.news_group_model,
            NewsDigestBatchDraft,
            system_prompt,
        )
        result = agent.run_sync(
            _build_batch_curation_prompt(clusters),
            model_settings={"timeout": settings.worker_timeout_seconds},
        )
        batch = result.output
        curated: list[NewsDigestCuratedBulletDraft] = []
        seen_ranks: set[int] = set()
        for draft in batch.bullets:
            if draft.cluster_rank in seen_ranks:
                continue
            cluster = cluster_by_rank.get(draft.cluster_rank)
            if cluster is None:
                continue
            curated.append(
                NewsDigestCuratedBulletDraft(
                    cluster=cluster,
                    draft=_validate_batch_bullet_draft(cluster=cluster, draft=draft),
                )
            )
            seen_ranks.add(draft.cluster_rank)
        if curated:
            return curated, True
        raise ValueError("Batch curation returned no valid bullets")
    except Exception as exc:  # noqa: BLE001
        logger.exception("News digest batch curation failed")
        raise RuntimeError("News digest batch curation failed") from exc


def _build_header_prompt(bullets: list[NewsDigestBulletDraft]) -> str:
    lines = [
        "Create the title and summary for this digest run.",
        "",
        "Bullets:",
    ]
    for bullet in bullets:
        lines.append(f"- {bullet.topic}: {bullet.details}")
    return "\n".join(lines)


def _fallback_header_draft(bullets: list[NewsDigestBulletDraft]) -> NewsDigestHeaderDraft:
    first = bullets[0]
    title = first.topic if len(bullets) == 1 else f"{first.topic} and {len(bullets) - 1} more"
    summary = " ".join(bullet.details for bullet in bullets[:2]).strip()
    return NewsDigestHeaderDraft(title=title[:240], summary=summary[:800])


def _generate_header_draft(bullets: list[NewsDigestBulletDraft]) -> NewsDigestHeaderDraft:
    settings = get_settings()
    try:
        agent = get_basic_agent(
            settings.news_header_model,
            NewsDigestHeaderDraft,
            HEADER_SYSTEM_PROMPT,
        )
        result = agent.run_sync(
            _build_header_prompt(bullets),
            model_settings={"timeout": settings.worker_timeout_seconds},
        )
        return result.output
    except Exception as exc:  # noqa: BLE001
        logger.exception("News digest header generation failed")
        raise RuntimeError("News digest header generation failed") from exc


def _coverage_item_ids_for_cluster(cluster: NewsDigestCluster) -> list[int]:
    return [item.id for item in cluster.items]


def _ensure_no_pending_generate_task(db: Session, *, user_id: int) -> bool:
    pending_tasks = (
        db.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.GENERATE_NEWS_DIGEST.value)
        .filter(ProcessingTask.status.in_([TaskStatus.PENDING.value, TaskStatus.PROCESSING.value]))
        .all()
    )
    for task in pending_tasks:
        payload = task.payload if isinstance(task.payload, dict) else {}
        if payload.get("user_id") == user_id:
            return False
    return True


def enqueue_news_digest_generation(
    db: Session,
    *,
    user_id: int,
    trigger_reason: str,
) -> int | None:
    """Enqueue one digest generation task when not already pending."""
    if not _ensure_no_pending_generate_task(db, user_id=user_id):
        return None

    from app.services.queue import get_queue_service

    queue_service = get_queue_service()
    return queue_service.enqueue(
        TaskType.GENERATE_NEWS_DIGEST,
        payload={"user_id": user_id, "trigger_reason": trigger_reason},
        dedupe=False,
    )


def generate_news_digest_for_user(
    db: Session,
    *,
    user_id: int,
    trigger_reason: str | None = None,
    force: bool = False,
    curated_bullet_generator: Callable[
        [User, list[NewsDigestCluster]],
        tuple[list[NewsDigestCuratedBulletDraft], bool],
    ]
    | None = None,
    header_draft_generator: Callable[[list[NewsDigestBulletDraft]], NewsDigestHeaderDraft]
    | None = None,
) -> NewsDigestGenerationResult:
    """Generate and persist one news digest run for a user."""
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if user is None:
        return NewsDigestGenerationResult(
            digest_id=None,
            source_count=0,
            group_count=0,
            trigger_reason=None,
            skipped=True,
        )

    decision = get_news_digest_trigger_decision(db, user=user)
    resolved_reason = trigger_reason or decision.trigger_reason
    if not force and not decision.should_generate:
        return NewsDigestGenerationResult(
            digest_id=None,
            source_count=decision.candidate_count,
            group_count=decision.provisional_group_count,
            trigger_reason=resolved_reason,
            skipped=True,
        )

    candidates = list_visible_uncovered_news_items(db, user_id=user.id)
    if not candidates:
        return NewsDigestGenerationResult(
            digest_id=None,
            source_count=0,
            group_count=0,
            trigger_reason=resolved_reason,
            skipped=True,
        )

    clusters = cluster_news_items(candidates)
    if not clusters:
        return NewsDigestGenerationResult(
            digest_id=None,
            source_count=len(candidates),
            group_count=0,
            trigger_reason=resolved_reason,
            skipped=True,
        )

    generator = curated_bullet_generator or (
        lambda current_user, current_clusters: _generate_curated_cluster_bullets(
            user=current_user,
            clusters=current_clusters,
        )
    )
    curated_bullets, used_batch_curation = generator(user, clusters)
    bullet_drafts = [entry.draft for entry in curated_bullets]
    header_generator = header_draft_generator or _generate_header_draft
    header_draft = header_generator(bullet_drafts)
    settings = get_settings()
    resolved_reason = resolved_reason or "manual"
    digest = NewsDigest(
        user_id=user.id,
        timezone=normalize_timezone(user.news_digest_timezone),
        window_start_at=min(
            (_coerce_utc(item.ingested_at) or _utcnow_naive()) for item in candidates
        ),
        window_end_at=max(
            (_coerce_utc(item.ingested_at) or _utcnow_naive()) for item in candidates
        ),
        title=header_draft.title,
        summary=header_draft.summary,
        source_count=len(candidates),
        group_count=len(curated_bullets),
        embedding_model=settings.news_embedding_model,
        llm_model=settings.news_group_model,
        pipeline_version=PIPELINE_VERSION,
        trigger_reason=resolved_reason,
        generated_at=_utcnow_naive(),
        build_metadata={
            "candidate_count": len(candidates),
            "raw_cluster_count": len(clusters),
            "curated_group_count": len(curated_bullets),
            "header_model": settings.news_header_model,
            "primary_threshold": settings.news_digest_primary_similarity_threshold,
            "secondary_threshold": settings.news_digest_secondary_similarity_threshold,
            "used_batch_curation": used_batch_curation,
        },
    )
    db.add(digest)
    db.flush()

    for position, entry in enumerate(curated_bullets, start=1):
        cluster = entry.cluster
        draft = entry.draft
        bullet = NewsDigestBullet(
            digest_id=digest.id,
            position=position,
            topic=draft.topic,
            details=draft.details,
            source_count=len(draft.news_item_ids),
        )
        db.add(bullet)
        db.flush()

        cited_ids = draft.news_item_ids or [item.id for item in cluster.items]
        for source_position, news_item_id in enumerate(cited_ids, start=1):
            db.add(
                NewsDigestBulletSource(
                    bullet_id=bullet.id,
                    news_item_id=news_item_id,
                    position=source_position,
                )
            )

        for coverage_item_id in _coverage_item_ids_for_cluster(cluster):
            db.add(
                NewsItemDigestCoverage(
                    user_id=user.id,
                    news_item_id=coverage_item_id,
                    digest_id=digest.id,
                )
            )

    db.flush()
    return NewsDigestGenerationResult(
        digest_id=digest.id,
        source_count=len(candidates),
        group_count=len(curated_bullets),
        trigger_reason=resolved_reason,
    )


def list_news_digests(
    db: Session,
    *,
    user_id: int,
    read_filter: str = "all",
    cursor: str | None = None,
    limit: int = 25,
) -> tuple[list[NewsDigest], PaginationMetadata]:
    """List digest runs for a user with cursor pagination."""
    last_id: int | None = None
    last_generated_at: datetime | None = None
    if cursor:
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8"))
        last_id = int(decoded["last_id"])
        last_generated_at = datetime.fromisoformat(decoded["last_generated_at"])

    query = db.query(NewsDigest).filter(NewsDigest.user_id == user_id)
    if read_filter == "read":
        query = query.filter(NewsDigest.read_at.is_not(None))
    elif read_filter == "unread":
        query = query.filter(NewsDigest.read_at.is_(None))

    if last_id is not None and last_generated_at is not None:
        query = query.filter(
            or_(
                NewsDigest.generated_at < last_generated_at,
                and_(
                    NewsDigest.generated_at == last_generated_at,
                    NewsDigest.id < last_id,
                ),
            )
        )

    rows = (
        query.order_by(NewsDigest.generated_at.desc(), NewsDigest.id.desc()).limit(limit + 1).all()
    )
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last_row = rows[-1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "last_id": last_row.id,
                    "last_generated_at": last_row.generated_at.isoformat(),
                },
                sort_keys=True,
            ).encode("utf-8")
        ).decode("utf-8")

    meta = PaginationMetadata(
        next_cursor=next_cursor,
        has_more=has_more,
        page_size=len(rows),
        total=None,
    )
    return rows, meta


def get_user_news_digest(db: Session, *, user_id: int, digest_id: int) -> NewsDigest | None:
    """Return a user-owned digest run."""
    return (
        db.query(NewsDigest)
        .filter(NewsDigest.id == digest_id, NewsDigest.user_id == user_id)
        .first()
    )


def list_digest_bullets_with_sources(
    db: Session,
    *,
    digest_id: int,
) -> list[tuple[NewsDigestBullet, list[NewsItem]]]:
    """Load persisted bullets and their cited news items."""
    bullets = (
        db.query(NewsDigestBullet)
        .filter(NewsDigestBullet.digest_id == digest_id)
        .order_by(NewsDigestBullet.position.asc())
        .all()
    )
    if not bullets:
        return []

    bullet_ids = [bullet.id for bullet in bullets]
    source_rows = (
        db.query(NewsDigestBulletSource)
        .filter(NewsDigestBulletSource.bullet_id.in_(bullet_ids))
        .order_by(
            NewsDigestBulletSource.bullet_id.asc(),
            NewsDigestBulletSource.position.asc(),
        )
        .all()
    )
    item_ids = [row.news_item_id for row in source_rows]
    items_by_id: dict[int, NewsItem] = {}
    if item_ids:
        items = db.query(NewsItem).filter(NewsItem.id.in_(item_ids)).all()
        items_by_id = {item.id: item for item in items}

    source_ids_by_bullet: dict[int, list[int]] = {}
    for row in source_rows:
        source_ids_by_bullet.setdefault(row.bullet_id, []).append(row.news_item_id)

    results: list[tuple[NewsDigestBullet, list[NewsItem]]] = []
    for bullet in bullets:
        cited_items = [
            items_by_id[item_id]
            for item_id in source_ids_by_bullet.get(bullet.id, [])
            if item_id in items_by_id
        ]
        results.append((bullet, cited_items))
    return results


def resolve_news_item_outward_url(item: NewsItem) -> str | None:
    """Resolve the most useful outward citation URL for a news item."""
    return _resolve_outward_url(item)


def calculate_pairwise_cluster_counts(clusters: list[NewsDigestCluster]) -> tuple[int, int]:
    """Return simple pairwise totals for eval reporting."""
    positive_pairs = 0
    item_count = 0
    for cluster in clusters:
        cluster_size = len(cluster.items)
        item_count += cluster_size
        positive_pairs += math.comb(cluster_size, 2) if cluster_size >= 2 else 0
    return positive_pairs, item_count
