#!/usr/bin/env python3
"""Run fixture-backed summary generation evals."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    from app.services.summary_eval import (
        DEFAULT_SUMMARY_EVAL_DATASET,
        load_summary_eval_suite,
        run_summary_eval_suite,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_SUMMARY_EVAL_DATASET),
        help="Path to the summary eval YAML dataset.",
    )
    parser.add_argument(
        "--case",
        default=None,
        help="Optional single case ID to run.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override generation model spec.",
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help="Override judge model spec.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output the full report as JSON.",
    )
    args = parser.parse_args()

    suite = load_summary_eval_suite(args.dataset)
    report = run_summary_eval_suite(
        suite,
        case_id=args.case,
        model_spec=args.model,
        judge_model_spec=args.judge_model,
    )

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return

    for result in report.results:
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.case_id}")
        if result.existing_title:
            print(f"  existing: {result.existing_title}")
        if result.generated_title:
            print(f"  generated: {result.generated_title}")
        if result.score is not None:
            print(f"  score: {result.score:.2f}")
        if result.reasoning:
            print(f"  judge: {result.reasoning}")
        if result.error:
            print(f"  error: {result.error}")


if __name__ == "__main__":
    main()
