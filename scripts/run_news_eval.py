"""Run feed-style relation evals against the frozen short-form news corpus."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from itertools import combinations
from pathlib import Path
from time import perf_counter
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logging import get_logger, setup_logging
from app.core.settings import get_settings
from app.models.schema import NewsItem
from app.services.news_embeddings import encode_news_texts
from app.services.news_relations import exact_relation_key, match_tokens_for_text, matching_text

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate news-native clustering slices")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("tests/evals/news_shortform"),
        help="Directory containing exported JSONL slices",
    )
    parser.add_argument(
        "--slice",
        action="append",
        dest="slices",
        help="Optional slice name(s) to evaluate",
    )
    settings = get_settings()
    parser.add_argument(
        "--primary-threshold",
        type=float,
        default=settings.news_list_primary_similarity_threshold,
        help="Primary semantic similarity threshold",
    )
    parser.add_argument(
        "--secondary-threshold",
        type=float,
        default=settings.news_list_secondary_similarity_threshold,
        help="Secondary semantic similarity threshold",
    )
    parser.add_argument(
        "--require-guard-for-primary",
        action="store_true",
        help="Require the lexical guard for primary-threshold matches too",
    )
    parser.add_argument(
        "--min-title-token-overlap",
        type=int,
        default=1,
        help="Minimum normalized title-token overlap for non-exact semantic merges",
    )
    parser.add_argument(
        "--disable-source-or-domain-shortcut",
        action="store_true",
        help="Do not let same source/domain satisfy the lexical guard by itself",
    )
    parser.add_argument(
        "--block-conflicting-exact-keys",
        action="store_true",
        help="Skip semantic merges when both items already have different strong exact keys",
    )
    return parser.parse_args()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _build_news_item(record: dict[str, Any]) -> NewsItem:
    return NewsItem(
        id=int(record["legacy_content_id"]),
        ingest_key=f"eval-{record['legacy_content_id']}",
        visibility_scope=str(record.get("visibility_scope") or "global"),
        owner_user_id=record.get("owner_user_id"),
        platform=record.get("platform"),
        source_type=record.get("source_type"),
        source_label=record.get("source_label"),
        source_external_id=record.get("source_external_id"),
        canonical_item_url=record.get("canonical_item_url"),
        canonical_story_url=record.get("canonical_story_url"),
        article_url=record.get("article_url"),
        article_title=record.get("article_title"),
        article_domain=record.get("article_domain"),
        discussion_url=record.get("discussion_url"),
        summary_title=record.get("summary_title"),
        summary_key_points=record.get("summary_key_points") or [],
        summary_text=record.get("summary_text"),
        raw_metadata=record.get("raw_metadata") or {},
        status=str(record.get("status") or "ready"),
        published_at=_parse_datetime(record.get("published_at")),
        ingested_at=_parse_datetime(record.get("ingested_at")),
    )


def _pairwise_sets(
    item_ids: list[int],
    labels_by_id: Mapping[int, str | None],
) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for left, right in combinations(sorted(item_ids), 2):
        left_label = labels_by_id.get(left)
        right_label = labels_by_id.get(right)
        if left_label is None or right_label is None:
            continue
        if left_label == right_label:
            pairs.add((left, right))
    return pairs


def _title_tokens(item: NewsItem) -> set[str]:
    title = item.summary_title or item.article_title or ""
    return match_tokens_for_text(title.casefold())


def _guard_passes(
    left: NewsItem,
    right: NewsItem,
    *,
    min_title_token_overlap: int,
    allow_source_or_domain_shortcut: bool,
) -> bool:
    overlap = len(_title_tokens(left) & _title_tokens(right))
    if overlap >= min_title_token_overlap:
        return True

    if not allow_source_or_domain_shortcut:
        return False

    left_domain = (left.article_domain or "").strip().casefold()
    right_domain = (right.article_domain or "").strip().casefold()
    if left_domain and left_domain == right_domain:
        return True

    left_source = (left.source_label or "").strip().casefold()
    right_source = (right.source_label or "").strip().casefold()
    return bool(left_source and left_source == right_source)


def _find_related_representative(
    item: NewsItem,
    representatives: list[NewsItem],
    *,
    primary_threshold: float,
    secondary_threshold: float,
    require_guard_for_primary: bool,
    min_title_token_overlap: int,
    allow_source_or_domain_shortcut: bool,
    block_conflicting_exact_keys: bool,
) -> NewsItem | None:
    if not representatives:
        return None

    item_exact_key = exact_relation_key(item)
    if item_exact_key is not None:
        for representative in representatives:
            if exact_relation_key(representative) == item_exact_key:
                return representative

    item_text = matching_text(item)
    representative_texts = [matching_text(representative) for representative in representatives]
    vectors = encode_news_texts([item_text, *representative_texts])
    if vectors.size == 0:
        return None

    scores = vectors[0] @ vectors[1:].T
    best_representative: NewsItem | None = None
    best_score = -1.0
    for index, representative in enumerate(representatives):
        representative_exact_key = exact_relation_key(representative)
        if (
            block_conflicting_exact_keys
            and item_exact_key is not None
            and representative_exact_key is not None
            and item_exact_key != representative_exact_key
        ):
            continue

        passes_guard = _guard_passes(
            item,
            representative,
            min_title_token_overlap=min_title_token_overlap,
            allow_source_or_domain_shortcut=allow_source_or_domain_shortcut,
        )
        score = float(scores[index])
        if score >= primary_threshold and score > best_score:
            if require_guard_for_primary and not passes_guard:
                continue
            best_representative = representative
            best_score = score
            continue
        if score >= secondary_threshold and score > best_score and passes_guard:
            best_representative = representative
            best_score = score

    return best_representative


def _cluster_feed_items(
    items: list[NewsItem],
    *,
    primary_threshold: float,
    secondary_threshold: float,
    require_guard_for_primary: bool,
    min_title_token_overlap: int,
    allow_source_or_domain_shortcut: bool,
    block_conflicting_exact_keys: bool,
) -> list[list[NewsItem]]:
    representatives: list[NewsItem] = []
    clusters_by_representative_id: dict[int, list[NewsItem]] = {}
    ordered_items = sorted(items, key=lambda item: (item.ingested_at or datetime.min, item.id or 0))

    for item in ordered_items:
        item_id = item.id
        if item_id is None:
            raise ValueError("News item missing id")
        representative = _find_related_representative(
            item,
            representatives,
            primary_threshold=primary_threshold,
            secondary_threshold=secondary_threshold,
            require_guard_for_primary=require_guard_for_primary,
            min_title_token_overlap=min_title_token_overlap,
            allow_source_or_domain_shortcut=allow_source_or_domain_shortcut,
            block_conflicting_exact_keys=block_conflicting_exact_keys,
        )
        if representative is None:
            representatives.append(item)
            clusters_by_representative_id[item_id] = [item]
            continue
        representative_id = representative.id
        if representative_id is None:
            raise ValueError("Representative item missing id")
        clusters_by_representative_id[representative_id].append(item)

    return list(clusters_by_representative_id.values())


def _pairwise_positive_count(predicted_clusters: list[list[NewsItem]]) -> tuple[int, int]:
    positive_pairs = 0
    item_count = 0
    for cluster in predicted_clusters:
        cluster_size = len(cluster)
        item_count += cluster_size
        if cluster_size >= 2:
            positive_pairs += cluster_size * (cluster_size - 1) // 2
    return positive_pairs, item_count


def _score_case(records: list[dict[str, Any]], *, args: argparse.Namespace) -> dict[str, float]:
    items = [_build_news_item(record) for record in records]
    started_at = perf_counter()
    predicted_clusters = _cluster_feed_items(
        items,
        primary_threshold=args.primary_threshold,
        secondary_threshold=args.secondary_threshold,
        require_guard_for_primary=args.require_guard_for_primary,
        min_title_token_overlap=args.min_title_token_overlap,
        allow_source_or_domain_shortcut=not args.disable_source_or_domain_shortcut,
        block_conflicting_exact_keys=args.block_conflicting_exact_keys,
    )
    runtime_ms = (perf_counter() - started_at) * 1000

    gold_labels = {
        int(record["legacy_content_id"]): record.get("gold_cluster_id") for record in records
    }
    predicted_labels: dict[int, str] = {}
    for cluster_index, cluster in enumerate(predicted_clusters, start=1):
        label = f"pred:{cluster_index}"
        for item in cluster:
            item_id = item.id
            if item_id is None:
                raise ValueError("Predicted cluster item missing id")
            predicted_labels[item_id] = label

    item_ids = [item.id for item in items if item.id is not None]
    gold_pairs = _pairwise_sets(item_ids, gold_labels)
    predicted_pairs = _pairwise_sets(item_ids, predicted_labels)
    true_positive = len(gold_pairs & predicted_pairs)
    false_positive = len(predicted_pairs - gold_pairs)
    precision = true_positive / len(predicted_pairs) if predicted_pairs else 1.0
    recall = true_positive / len(gold_pairs) if gold_pairs else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    over_merge_rate = false_positive / len(predicted_pairs) if predicted_pairs else 0.0
    pairwise_positive_pairs, item_count = _pairwise_positive_count(predicted_clusters)
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "over_merge_rate": over_merge_rate,
        "runtime_ms": runtime_ms,
        "predicted_positive_pairs": float(pairwise_positive_pairs),
        "item_count": float(item_count),
        "citation_validity": 1.0,
    }


def _aggregate_case_scores(scores: list[dict[str, float]]) -> dict[str, float]:
    if not scores:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "over_merge_rate": 0.0,
            "runtime_ms": 0.0,
            "case_count": 0.0,
            "citation_validity": 0.0,
        }

    keys = (
        "precision",
        "recall",
        "f1",
        "over_merge_rate",
        "runtime_ms",
        "citation_validity",
    )
    return {
        **{key: sum(score[key] for score in scores) / len(scores) for key in keys},
        "case_count": float(len(scores)),
    }


def main() -> None:
    setup_logging()
    args = _parse_args()
    requested_slices = args.slices or [
        "exact_duplicates",
        "mixed_source_windows",
        "user_scoped_x_windows",
    ]

    summaries: dict[str, dict[str, float]] = {}
    for slice_name in requested_slices:
        records = _read_jsonl(args.input_dir / f"{slice_name}.jsonl")
        cases: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            cases[str(record.get("case_id") or "unknown")].append(record)

        case_scores = [
            _score_case(case_records, args=args) for case_records in cases.values() if case_records
        ]
        summaries[slice_name] = _aggregate_case_scores(case_scores)

    print(
        json.dumps(
            {
                "config": {
                    "primary_threshold": args.primary_threshold,
                    "secondary_threshold": args.secondary_threshold,
                    "require_guard_for_primary": args.require_guard_for_primary,
                    "min_title_token_overlap": args.min_title_token_overlap,
                    "allow_source_or_domain_shortcut": (not args.disable_source_or_domain_shortcut),
                    "block_conflicting_exact_keys": args.block_conflicting_exact_keys,
                },
                "summaries": summaries,
            },
            indent=2,
            sort_keys=True,
        )
    )
    logger.info(
        "Completed news eval",
        extra={
            "component": "news_eval",
            "operation": "run_eval",
            "context_data": {"slices": requested_slices, "summaries": summaries},
        },
    )


if __name__ == "__main__":
    main()
