#!/usr/bin/env python3
"""Run production-backed Briefing layout evals."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    from scripts.support.briefing_eval import (
        DEFAULT_BRIEFING_EVAL_DATASET,
        OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC,
        load_briefing_eval_suite,
        run_briefing_eval_suite,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_BRIEFING_EVAL_DATASET),
        help="Path to the Briefing eval YAML dataset.",
    )
    parser.add_argument("--case", default=None, help="Optional single case ID to run.")
    parser.add_argument("--model", default=None, help="Override the live generation model spec.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call the model instead of replaying the frozen malformed fixture.",
    )
    parser.add_argument("--json", action="store_true", help="Output the full report as JSON.")
    args = parser.parse_args()

    suite = load_briefing_eval_suite(args.dataset)
    report = run_briefing_eval_suite(
        suite,
        mode="live" if args.live else "fixture",
        case_id=args.case,
        model_spec=args.model or OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC,
    )

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        for result in report.results:
            status = "PASS" if result.gate_satisfied else "FAIL"
            print(f"[{status}] {result.case_id} ({result.mode})")
            if result.raw_assessment:
                print(f"  raw disposition: {result.raw_assessment.disposition.value}")
                print(f"  raw layout valid: {result.raw_layout_valid}")
                if result.raw_assessment.low_signal_values:
                    print("  raw low-signal: " + ", ".join(result.raw_assessment.low_signal_values))
                if result.raw_assessment.coverage.missing_source_keys:
                    print(
                        "  raw missing sources: "
                        + ", ".join(result.raw_assessment.coverage.missing_source_keys)
                    )
            if result.final_assessment:
                print(f"  final disposition: {result.final_assessment.disposition.value}")
            print(f"  final layout valid: {result.layout_valid}")
            if result.expectation_met is not None:
                print(f"  expectation met: {result.expectation_met}")
            if result.warnings:
                print("  warnings: " + ", ".join(result.warnings))
            if result.error:
                print(f"  error: {result.error}")

    if not report.gate_satisfied:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
