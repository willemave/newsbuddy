#!/usr/bin/env python3
"""Evaluate feed detection heuristics vs LLM classification.

Optionally evaluates labeled RSS configs (substack/atom/podcasts) and/or
inspects page URLs (articles/news) to compare heuristic vs LLM detection.
"""

from __future__ import annotations

# ruff: noqa: E402
import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.feed_detection import FeedDetector, classify_feed_type_with_llm
from app.services.http import get_http_service

DEFAULT_MODELS = [
    "openai:gpt-5.4",
    "google-gla:gemini-3-flash-preview",
]


def _load_yaml_feeds(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    feeds = data.get("feeds", [])
    results: list[dict[str, Any]] = []
    for feed in feeds:
        if isinstance(feed, dict) and feed.get("url"):
            results.append(feed)
        elif isinstance(feed, str):
            results.append({"url": feed})
    return results


def _build_labeled_cases(config_dir: Path, limit: int | None) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    sources = [
        ("substack.yml", "substack"),
        ("atom.yml", "atom"),
        ("podcasts.yml", "podcast_rss"),
    ]
    for filename, feed_type in sources:
        feeds = _load_yaml_feeds(config_dir / filename)
        if limit:
            feeds = feeds[:limit]
        for feed in feeds:
            feed_url = feed.get("url")
            if isinstance(feed_url, str) and feed_url.strip():
                cases.append({"feed_url": feed_url, "expected": feed_type})
    return cases


def _evaluate_heuristic_cases(
    detector: FeedDetector,
    cases: list[dict[str, str]],
) -> dict[str, Any]:
    hits = 0
    rows = []
    for case in cases:
        feed_url = case["feed_url"]
        expected = case["expected"]
        result = detector.classify_feed_type(
            feed_url=feed_url,
            page_url=feed_url,
            page_title=None,
            html_content=None,
        )
        match = result.feed_type == expected
        hits += int(match)
        rows.append(
            {
                "feed_url": feed_url,
                "expected": expected,
                "predicted": result.feed_type,
                "confidence": result.confidence,
                "reasoning": result.reasoning,
            }
        )
    total = len(cases)
    accuracy = hits / total if total else 0.0
    return {"total": total, "hits": hits, "accuracy": accuracy, "rows": rows}


def _evaluate_llm_cases(
    cases: list[dict[str, str]],
    *,
    model_spec: str,
) -> dict[str, Any]:
    hits = 0
    rows = []
    for case in cases:
        feed_url = case["feed_url"]
        expected = case["expected"]
        result = classify_feed_type_with_llm(
            feed_url=feed_url,
            page_url=feed_url,
            page_title=None,
            model_spec=model_spec,
        )
        predicted = result.feed_type if result else "error"
        match = predicted == expected
        hits += int(match)
        rows.append(
            {
                "feed_url": feed_url,
                "expected": expected,
                "predicted": predicted,
                "confidence": result.confidence if result else 0.0,
                "reasoning": result.reasoning if result else "LLM_error",
            }
        )
    total = len(cases)
    accuracy = hits / total if total else 0.0
    return {"total": total, "hits": hits, "accuracy": accuracy, "rows": rows}


def _fetch_html(url: str) -> str | None:
    http_service = get_http_service()
    body, _headers = http_service.fetch_content(url)
    if isinstance(body, str):
        return body
    return None


def _inspect_page_urls(
    detector: FeedDetector,
    urls: list[str],
    *,
    source: str,
    content_type: str,
    model_spec: str | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for url in urls:
        html_content = _fetch_html(url)
        if not html_content:
            results.append({"url": url, "error": "fetch_failed"})
            continue
        feed_data = detector.detect_from_html(
            html_content,
            url,
            page_title=None,
            source=source,
            content_type=content_type,
            model_spec=model_spec,
        )
        if feed_data and feed_data.get("detected_feed"):
            detected = feed_data["detected_feed"]
            model_name = model_spec or "heuristic"
            results.append(
                {
                    "url": url,
                    "detected_feed": detected,
                    "all_detected_feeds": feed_data.get("all_detected_feeds"),
                    "model": model_name,
                }
            )
        else:
            results.append(
                {
                    "url": url,
                    "detected_feed": None,
                    "model": model_spec or "heuristic",
                }
            )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-dir",
        default=str(ROOT / "config"),
        help="Directory containing substack.yml/atom.yml/podcasts.yml",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit feeds per config to keep evaluation quick",
    )
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="Comma-separated model specs for LLM evaluation",
    )
    parser.add_argument(
        "--skip-config",
        action="store_true",
        help="Skip labeled feed evaluation from config/*.yml files",
    )
    parser.add_argument(
        "--page-url",
        action="append",
        default=[],
        help="Page URL to test end-to-end feed detection",
    )
    parser.add_argument(
        "--page-urls-file",
        default=None,
        help="File containing newline-separated page URLs",
    )
    parser.add_argument(
        "--page-source",
        default="self submission",
        help="Source to pass into feed detection for page URLs",
    )
    parser.add_argument(
        "--page-content-type",
        default="article",
        help="Content type to pass into feed detection for page URLs",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of formatted text",
    )
    args = parser.parse_args()

    llm_results: dict[str, Any] = {}
    heuristic_results: dict[str, Any] | None = None
    if not args.skip_config:
        config_dir = Path(args.config_dir)
        cases = _build_labeled_cases(config_dir, args.limit)

        heuristic_detector = FeedDetector(use_llm=False)
        heuristic_results = _evaluate_heuristic_cases(heuristic_detector, cases)

        for model_spec in [m.strip() for m in args.models.split(",") if m.strip()]:
            llm_results[model_spec] = _evaluate_llm_cases(
                cases,
                model_spec=model_spec,
            )

    page_urls = list(args.page_url)
    if args.page_urls_file:
        page_path = Path(args.page_urls_file)
        if page_path.exists():
            page_urls.extend(
                [line.strip() for line in page_path.read_text().splitlines() if line.strip()]
            )

    page_inspection = None
    if page_urls:
        page_inspection = {
            "heuristic": _inspect_page_urls(
                FeedDetector(use_llm=False),
                page_urls,
                source=args.page_source,
                content_type=args.page_content_type,
            ),
        }
        for model_spec in llm_results or [m.strip() for m in args.models.split(",") if m.strip()]:
            page_inspection[model_spec] = _inspect_page_urls(
                FeedDetector(use_llm=True),
                page_urls,
                source=args.page_source,
                content_type=args.page_content_type,
                model_spec=model_spec,
            )

    output = {
        "heuristic": heuristic_results,
        "llm": llm_results,
        "page_inspection": page_inspection,
    }

    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
        return

    if heuristic_results is not None:
        print("Heuristic accuracy:")
        print(
            f"  {heuristic_results['hits']}/{heuristic_results['total']}"
            f" ({heuristic_results['accuracy']:.2%})"
        )
        for model_spec, result in llm_results.items():
            print(f"LLM accuracy ({model_spec}):")
            print(f"  {result['hits']}/{result['total']} ({result['accuracy']:.2%})")

    if page_inspection:
        print("\nPage URL inspection:")
        for label, rows in page_inspection.items():
            print(f"  {label}:")
            for row in rows:
                detected = row.get("detected_feed")
                status = detected.get("url") if isinstance(detected, dict) else "none"
                print(f"    {row['url']} -> {status}")


if __name__ == "__main__":
    main()
