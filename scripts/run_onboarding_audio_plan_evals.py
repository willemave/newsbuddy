#!/usr/bin/env python3
"""Compare onboarding audio-plan tool calls across fast inference routes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    """Print a dry-run plan or execute the paid eval explicitly."""
    from app.services.onboarding_eval import (
        DEFAULT_JUDGE_MODEL,
        DEFAULT_ONBOARDING_EVAL_CANDIDATE_ALIASES,
        estimate_eval_call_count,
        load_onboarding_audio_plan_eval_suite,
        resolve_onboarding_eval_candidates,
        run_onboarding_audio_plan_eval_suite,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        default="tests/evals/onboarding_audio_plan.yaml",
        help="YAML dataset containing transcripts and expected outcomes.",
    )
    parser.add_argument(
        "--candidates",
        nargs="+",
        default=list(DEFAULT_ONBOARDING_EVAL_CANDIDATE_ALIASES),
        help="Candidate aliases to compare.",
    )
    parser.add_argument("--runs", type=int, default=1, help="Repetitions per case/candidate.")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=15)
    parser.add_argument("--codex-timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Make live paid model calls. Without this flag the command is a dry run.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path for the complete JSON report.",
    )
    args = parser.parse_args()

    suite = load_onboarding_audio_plan_eval_suite(args.dataset)
    candidates = resolve_onboarding_eval_candidates(args.candidates)
    call_count = estimate_eval_call_count(
        case_count=len(suite.cases),
        candidate_count=len(candidates),
        runs=args.runs,
        judge=not args.no_judge,
    )
    if not args.execute:
        payload = {
            "dry_run": True,
            "suite": suite.suite,
            "cases": len(suite.cases),
            "candidates": [candidate.alias for candidate in candidates],
            "runs": args.runs,
            "judge_model": None if args.no_judge else args.judge_model,
            "live_call_count": call_count,
            "candidate_provider_calls": len(suite.cases) * len(candidates) * args.runs,
            "local_codex_calls": 0
            if args.no_judge
            else len(suite.cases) * len(candidates) * args.runs,
            "execute_flag_required": True,
        }
        print(json.dumps(payload, indent=2))
        return

    report = run_onboarding_audio_plan_eval_suite(
        suite,
        candidates=candidates,
        runs=args.runs,
        judge_model=args.judge_model,
        judge=not args.no_judge,
        timeout_seconds=args.timeout_seconds,
        codex_timeout_seconds=args.codex_timeout_seconds,
    )
    report_json = json.dumps(report.model_dump(mode="json"), indent=2)
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.write_text(report_json, encoding="utf-8")
        print(f"Wrote report to {output_path}")
        return
    print(report_json)


if __name__ == "__main__":
    main()
