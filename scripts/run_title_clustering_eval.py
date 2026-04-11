"""Run curated title-clustering eval families against the real relation matcher."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from itertools import combinations
from typing import Any, cast

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import get_session_factory
from app.core.logging import setup_logging
from app.core.settings import get_settings
from app.models.schema import NewsItem
from app.services.news_relations import reconcile_news_item_relation
from app.services.news_reranker import clear_news_reranker_cache
from tests.services.news_relation_cluster_cases import (
    NEGATIVE_PRODUCTION_CLUSTER_CASES,
    PRODUCTION_CLUSTER_CASES,
)

EVAL_OWNER_USER_ID = 9_999_999


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate curated title-clustering families with real embeddings"
    )
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="Optional case id(s) to run",
    )
    parser.add_argument(
        "--failures-only",
        action="store_true",
        help="Print only failed cases in the text output",
    )
    parser.add_argument(
        "--threshold",
        action="append",
        dest="thresholds",
        help=("Optional threshold spec in label:primary:secondary[:reranker] format (repeatable)"),
    )
    parser.add_argument(
        "--use-reranker",
        action="store_true",
        help="Enable the Qwen reranker for merge decisions",
    )
    parser.add_argument(
        "--reranker-model",
        default=None,
        help="Optional reranker model override when --use-reranker is set",
    )
    parser.add_argument(
        "--reranker-max-candidates",
        type=int,
        default=None,
        help="Optional reranker candidate cap override when --use-reranker is set",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of text",
    )
    return parser.parse_args()


def _make_item(
    *,
    idx: int,
    title: str,
    case_id: str,
    ingested_at: datetime,
) -> NewsItem:
    return NewsItem(
        ingest_key=f"eval-{case_id}-{idx}",
        visibility_scope="user",
        owner_user_id=EVAL_OWNER_USER_ID,
        platform="hackernews",
        source_type="hackernews",
        source_label=f"Source {idx}",
        source_external_id=f"{case_id}-{idx}",
        canonical_item_url=f"https://news.ycombinator.com/item?id={case_id}-{idx}",
        canonical_story_url=f"https://example.com/{case_id}/{idx}",
        article_url=f"https://example.com/{case_id}/{idx}",
        article_title=title,
        article_domain=f"source{idx}.example.com",
        discussion_url=f"https://news.ycombinator.com/item?id={case_id}-{idx}",
        summary_title=title,
        summary_key_points=[],
        summary_text=title,
        raw_metadata={},
        status="ready",
        ingested_at=ingested_at,
        processed_at=ingested_at,
    )


def _select_cases(case_ids: set[str] | None) -> list[dict[str, Any]]:
    all_cases = cast(
        list[dict[str, Any]],
        [*PRODUCTION_CLUSTER_CASES, *NEGATIVE_PRODUCTION_CLUSTER_CASES],
    )
    if not case_ids:
        return all_cases
    return [case for case in all_cases if str(case["case_id"]) in case_ids]


def _parse_thresholds(
    raw_specs: list[str] | None,
    *,
    use_reranker: bool,
) -> list[dict[str, Any]]:
    if not raw_specs:
        settings = get_settings()
        return [
            {
                "label": "current",
                "primary": settings.news_list_primary_similarity_threshold,
                "secondary": settings.news_list_secondary_similarity_threshold,
                "reranker": settings.news_list_reranker_similarity_threshold,
                "use_reranker": use_reranker,
            }
        ]

    thresholds: list[dict[str, Any]] = []
    default_reranker_threshold = get_settings().news_list_reranker_similarity_threshold
    for raw_spec in raw_specs:
        parts = raw_spec.split(":")
        if len(parts) not in {3, 4}:
            raise ValueError(
                f"Invalid threshold spec {raw_spec!r}; expected label:primary:secondary[:reranker]"
            )
        label, primary_raw, secondary_raw = parts[:3]
        thresholds.append(
            {
                "label": label.strip(),
                "primary": float(primary_raw),
                "secondary": float(secondary_raw),
                "reranker": (float(parts[3]) if len(parts) == 4 else default_reranker_threshold),
                "use_reranker": use_reranker,
            }
        )
    return thresholds


@contextmanager
def _temporary_thresholds(
    *,
    primary: float,
    secondary: float,
    use_reranker: bool,
    reranker_threshold: float,
    reranker_model: str | None,
    reranker_max_candidates: int | None,
) -> Iterator[None]:
    previous = {
        "NEWS_LIST_PRIMARY_SIMILARITY_THRESHOLD": os.environ.get(
            "NEWS_LIST_PRIMARY_SIMILARITY_THRESHOLD"
        ),
        "NEWS_LIST_SECONDARY_SIMILARITY_THRESHOLD": os.environ.get(
            "NEWS_LIST_SECONDARY_SIMILARITY_THRESHOLD"
        ),
        "NEWS_LIST_RERANKER_ENABLED": os.environ.get("NEWS_LIST_RERANKER_ENABLED"),
        "NEWS_LIST_RERANKER_SIMILARITY_THRESHOLD": os.environ.get(
            "NEWS_LIST_RERANKER_SIMILARITY_THRESHOLD"
        ),
        "NEWS_LIST_RERANKER_MODEL": os.environ.get("NEWS_LIST_RERANKER_MODEL"),
        "NEWS_LIST_RERANKER_MAX_CANDIDATES": os.environ.get("NEWS_LIST_RERANKER_MAX_CANDIDATES"),
    }
    reranker_model_changed = (
        reranker_model is not None and reranker_model != previous["NEWS_LIST_RERANKER_MODEL"]
    )
    os.environ["NEWS_LIST_PRIMARY_SIMILARITY_THRESHOLD"] = str(primary)
    os.environ["NEWS_LIST_SECONDARY_SIMILARITY_THRESHOLD"] = str(secondary)
    os.environ["NEWS_LIST_RERANKER_ENABLED"] = "true" if use_reranker else "false"
    os.environ["NEWS_LIST_RERANKER_SIMILARITY_THRESHOLD"] = str(reranker_threshold)
    if reranker_model is not None:
        os.environ["NEWS_LIST_RERANKER_MODEL"] = reranker_model
    if reranker_max_candidates is not None:
        os.environ["NEWS_LIST_RERANKER_MAX_CANDIDATES"] = str(reranker_max_candidates)
    get_settings.cache_clear()
    if reranker_model_changed:
        clear_news_reranker_cache()
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        if reranker_model_changed:
            clear_news_reranker_cache()


def _case_groups(case: dict[str, Any]) -> list[list[str]]:
    raw_groups = case.get("groups")
    if isinstance(raw_groups, list) and raw_groups:
        return [[str(title) for title in group] for group in raw_groups]
    return [[str(title) for title in case["titles"]]]


def _pairwise_sets(labels_by_id: dict[int, str]) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for left, right in combinations(sorted(labels_by_id), 2):
        if labels_by_id[left] == labels_by_id[right]:
            pairs.add((left, right))
    return pairs


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    case_count = len(results)
    passed_count = sum(1 for result in results if result["passed"])
    return {
        "case_count": case_count,
        "passed_count": passed_count,
        "failed_count": case_count - passed_count,
        "macro_precision": (
            sum(result["precision"] for result in results) / case_count if case_count else 0.0
        ),
        "macro_recall": (
            sum(result["recall"] for result in results) / case_count if case_count else 0.0
        ),
        "macro_f1": sum(result["f1"] for result in results) / case_count if case_count else 0.0,
    }


def _evaluate_case(db, case: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN001
    created_ids: list[int] = []
    gold_labels_by_id: dict[int, str] = {}
    base_time = datetime.now(UTC).replace(tzinfo=None)
    label = str(case["label"])
    groups = _case_groups(case)
    case_id = str(case["case_id"])

    idx = 0
    for group_index, group in enumerate(groups, start=1):
        for title in group:
            item = _make_item(
                idx=idx,
                title=title,
                case_id=case_id,
                ingested_at=base_time + timedelta(seconds=idx),
            )
            db.add(item)
            db.flush()
            item_id = item.id
            if item_id is None:
                raise ValueError("Persisted news item missing id")
            reconcile_news_item_relation(db, news_item_id=item_id)
            created_ids.append(item_id)
            gold_labels_by_id[item_id] = f"gold:{group_index}"
            idx += 1

    predicted_labels_by_id: dict[int, str] = {}
    predicted_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item_id in created_ids:
        row = db.get(NewsItem, item_id)
        if row is None or row.id is None:
            continue
        representative_id = row.representative_news_item_id or row.id
        predicted_labels_by_id[row.id] = f"pred:{representative_id}"
        predicted_groups[representative_id].append(
            {
                "id": row.id,
                "representative_id": representative_id,
                "title": row.summary_title or row.article_title,
            }
        )

    gold_pairs = _pairwise_sets(gold_labels_by_id)
    predicted_pairs = _pairwise_sets(predicted_labels_by_id)
    true_positive = len(gold_pairs & predicted_pairs)
    precision = true_positive / len(predicted_pairs) if predicted_pairs else 1.0
    recall = true_positive / len(gold_pairs) if gold_pairs else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    representative_groups = sorted(
        (
            {
                "representative_id": representative_id,
                "member_count": len(members),
                "titles": [member["title"] for member in members],
            }
            for representative_id, members in predicted_groups.items()
        ),
        key=lambda group: (
            -cast(int, group["member_count"]),
            cast(int, group["representative_id"]),
        ),
    )
    return {
        "case_id": case_id,
        "label": label,
        "expected_member_count": len(created_ids),
        "gold_cluster_count": len(groups),
        "predicted_cluster_count": len(predicted_groups),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "passed": gold_pairs == predicted_pairs,
        "groups": representative_groups,
    }


def _print_text(summaries: list[dict[str, Any]], *, failures_only: bool) -> None:
    for run in summaries:
        aggregate = run["summary"]
        threshold = run["threshold"]
        reranker_suffix = f"({threshold['reranker']:.2f})" if threshold["use_reranker"] else ""
        print(
            f"[{threshold['label']}] Title clustering eval: "
            f"{aggregate['passed_count']}/{aggregate['case_count']} passed "
            f"macro_f1={aggregate['macro_f1']:.3f} "
            f"precision={aggregate['macro_precision']:.3f} "
            f"recall={aggregate['macro_recall']:.3f} "
            f"reranker={'on' if threshold['use_reranker'] else 'off'}"
            f"{reranker_suffix}"
        )
        rows = [result for result in run["results"] if not failures_only or not result["passed"]]
        for result in rows:
            status = "PASS" if result["passed"] else "FAIL"
            print(
                f"{status} {result['case_id']} "
                f"f1={result['f1']:.3f} "
                f"precision={result['precision']:.3f} "
                f"recall={result['recall']:.3f} "
                f"{result['label']}"
            )
            if result["passed"]:
                continue
            for group in result["groups"]:
                print(
                    f"  rep={group['representative_id']} members={group['member_count']} "
                    f"{group['titles'][0]}"
                )


def main() -> int:
    setup_logging()
    args = _parse_args()
    case_ids = set(args.case_ids or [])
    cases = _select_cases(case_ids or None)
    thresholds = _parse_thresholds(args.thresholds, use_reranker=args.use_reranker)
    session_factory = get_session_factory()
    runs: list[dict[str, Any]] = []

    for threshold in thresholds:
        results: list[dict[str, Any]] = []
        with _temporary_thresholds(
            primary=float(threshold["primary"]),
            secondary=float(threshold["secondary"]),
            use_reranker=bool(threshold["use_reranker"]),
            reranker_threshold=float(threshold["reranker"]),
            reranker_model=args.reranker_model,
            reranker_max_candidates=args.reranker_max_candidates,
        ):
            for case in cases:
                with session_factory() as db:
                    results.append(_evaluate_case(db, case))
                    db.rollback()
        runs.append(
            {
                "threshold": threshold,
                "summary": _aggregate(results),
                "results": results,
            }
        )

    if args.json:
        print(json.dumps({"runs": runs}, indent=2))
    else:
        _print_text(runs, failures_only=args.failures_only)

    return 0 if all(run["summary"]["failed_count"] == 0 for run in runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
