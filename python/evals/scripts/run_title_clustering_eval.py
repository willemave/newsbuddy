"""Run curated title clustering through Python embeddings and Rust policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from newsly_evals.encoders import SentenceTransformerEncoder
from newsly_evals.news_relation_cases import (
    NEGATIVE_PRODUCTION_CLUSTER_CASES,
    PRODUCTION_CLUSTER_CASES,
)
from newsly_evals.relations import run_relation_eval

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate Python-built embeddings with the production Rust relation policy"
    )
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--failures-only", action="store_true")
    parser.add_argument(
        "--threshold",
        action="append",
        dest="thresholds",
        help="label:primary:secondary (repeatable)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--trace", action="store_true")
    return parser.parse_args()


def _select_cases(case_ids: set[str] | None) -> list[dict[str, Any]]:
    cases = cast(
        list[dict[str, Any]],
        [*PRODUCTION_CLUSTER_CASES, *NEGATIVE_PRODUCTION_CLUSTER_CASES],
    )
    if not case_ids:
        return cases
    return [case for case in cases if str(case["case_id"]) in case_ids]


def _thresholds(raw_specs: list[str] | None) -> list[dict[str, Any]]:
    if not raw_specs:
        return [{"label": "current", "primary": 0.85, "secondary": 0.75}]
    parsed = []
    for spec in raw_specs:
        parts = spec.split(":")
        if len(parts) != 3:
            raise ValueError(f"invalid threshold {spec!r}; expected label:primary:secondary")
        label, primary, secondary = parts
        parsed.append(
            {
                "label": label.strip(),
                "primary": float(primary),
                "secondary": float(secondary),
            }
        )
    return parsed


def _print_text(payload: dict[str, Any], *, failures_only: bool) -> None:
    for run in payload["runs"]:
        summary = run["summary"]
        threshold = run["threshold"]
        print(
            f"[{threshold['label']}] Rust relation eval: "
            f"{summary['passed_count']}/{summary['case_count']} passed "
            f"macro_f1={summary['macro_f1']:.3f} "
            f"precision={summary['macro_precision']:.3f} "
            f"recall={summary['macro_recall']:.3f}"
        )
        for result in run["results"]:
            if failures_only and result["passed"]:
                continue
            status = "PASS" if result["passed"] else "FAIL"
            print(
                f"{status} {result['case_id']} f1={result['f1']:.3f} "
                f"precision={result['precision']:.3f} recall={result['recall']:.3f} "
                f"{result['label']}"
            )
            if result["passed"]:
                continue
            for group in result["groups"]:
                first_title = next((title for title in group["titles"] if title), "-")
                print(
                    f"  rep={group['representative_id']} members={group['member_count']} "
                    f"{first_title}"
                )
            for trace in result.get("traces", []):
                accepted = ",".join(str(value) for value in trace["accepted_ids"]) or "-"
                print(
                    f"  trace item={trace['item_id']} path={trace['path']} "
                    f"accepted=[{accepted}] {str(trace['item_title'])[:70]}"
                )


def main() -> int:
    args = _parse_args()
    result = run_relation_eval(
        raw_cases=_select_cases(set(args.case_ids or []) or None),
        encoder=SentenceTransformerEncoder(args.model),
        thresholds=_thresholds(args.thresholds),
        include_traces=args.trace,
        provider_metadata={"pipeline": "local_sentence_transformers"},
    )
    encoded = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    if args.json and not args.output:
        print(encoded)
    elif not args.json:
        _print_text(result, failures_only=args.failures_only)
    return int(any(run["summary"]["failed_count"] for run in result["runs"]))


if __name__ == "__main__":
    raise SystemExit(main())
