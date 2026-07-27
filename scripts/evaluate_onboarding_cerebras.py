#!/usr/bin/env python3
"""Quick evaluator for onboarding audio lane planning with Cerebras ZAI-GLM-4.7.

This script runs the same audio lane-planning prompt/schema used in onboarding
and reports shape/quality checks on normalized output.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow running as `python scripts/evaluate_onboarding_cerebras.py`
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.llm_agents import get_basic_agent
from app.services.onboarding import AUDIO_PLAN_SYSTEM_PROMPT
from app.services.onboarding.audio_plan_heuristics import (
    _normalize_audio_lane_plan_with_metadata,
)
from app.services.onboarding.internal_models import _AudioPlanOutput
from app.services.onboarding.llm_plans import _format_audio_plan_prompt

DEFAULT_CEREBRAS_MODEL = "cerebras:zai-glm-4.7"
DEFAULT_BASELINE_MODEL = "anthropic:claude-haiku-4-5-20251001"
DEFAULT_TIMEOUT_SECONDS = 12
DEFAULT_LOCALE = "en-US"
DEFAULT_TRANSCRIPTS = [
    "I want better biology and psychology podcasts with practical business ideas.",
    "I follow AI engineering, startup strategy, and product leadership.",
    "Help me find geopolitics analysis, macroeconomics explainers, and policy debate.",
]


@dataclass
class EvaluationReport:
    """Structured evaluation output for one model/transcript pair."""

    model: str
    transcript: str
    duration_ms: int
    success: bool
    error: str | None
    score: float
    checks: dict[str, bool]
    raw_output: dict[str, Any] | None
    normalized_output: dict[str, Any] | None
    used_normalization_fallback: bool | None


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Evaluate onboarding lane-plan quality with Cerebras ZAI-GLM-4.7."
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_CEREBRAS_MODEL,
        help=f"Model spec to evaluate (default: {DEFAULT_CEREBRAS_MODEL})",
    )
    parser.add_argument(
        "--compare-model",
        default=DEFAULT_BASELINE_MODEL,
        help=(
            "Optional baseline model spec for side-by-side output. "
            f"Default: {DEFAULT_BASELINE_MODEL}"
        ),
    )
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="Disable baseline comparison run.",
    )
    parser.add_argument("--transcript", default=None, help="Single transcript string to evaluate.")
    parser.add_argument(
        "--transcript-file",
        default=None,
        help="Path to a UTF-8 text file with one transcript per line.",
    )
    parser.add_argument(
        "--locale",
        default=DEFAULT_LOCALE,
        help=f"Locale value sent in prompt (default: {DEFAULT_LOCALE})",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-call timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of runs per transcript/model to sample consistency.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON report instead of concise text.",
    )
    return parser.parse_args()


def resolve_transcripts(args: argparse.Namespace) -> list[str]:
    """Resolve transcript inputs from CLI flags."""
    transcripts: list[str] = []
    if args.transcript:
        text = args.transcript.strip()
        if text:
            transcripts.append(text)

    if args.transcript_file:
        file_path = Path(args.transcript_file)
        lines = file_path.read_text(encoding="utf-8").splitlines()
        transcripts.extend(line.strip() for line in lines if line.strip())

    if transcripts:
        return transcripts
    return DEFAULT_TRANSCRIPTS


def extract_output(result: Any) -> Any:
    """Extract pydantic-ai output for different result wrappers."""
    if hasattr(result, "output"):
        return result.output
    if hasattr(result, "data"):
        return result.data
    raise AttributeError("Agent result missing output/data attribute")


def compute_checks(normalized: _AudioPlanOutput) -> tuple[dict[str, bool], float]:
    """Compute simple shape/quality checks for normalized onboarding plan."""
    lanes = list(normalized.lanes)
    lane_count = len(lanes)
    has_reddit_lane = any(lane.target == "reddit" for lane in lanes)

    query_counts = [len(lane.queries) for lane in lanes]
    all_query_count_ok = bool(query_counts) and all(2 <= count <= 4 for count in query_counts)

    query_word_counts = [len(query.split()) for lane in lanes for query in lane.queries]
    all_query_word_count_ok = bool(query_word_counts) and all(
        5 <= word_count <= 10 for word_count in query_word_counts
    )

    query_set = {query.lower().strip() for lane in lanes for query in lane.queries}
    total_queries = len([query for lane in lanes for query in lane.queries])
    unique_ratio = (len(query_set) / total_queries) if total_queries else 0.0
    query_variety_ok = unique_ratio >= 0.8

    inferred_topics_ok = 3 <= len(normalized.inferred_topics) <= 6
    summary_ok = len(normalized.topic_summary.strip()) >= 20
    lane_count_ok = 3 <= lane_count <= 5

    checks = {
        "lane_count_3_to_5": lane_count_ok,
        "has_reddit_lane": has_reddit_lane,
        "queries_per_lane_2_to_4": all_query_count_ok,
        "query_word_count_5_to_10": all_query_word_count_ok,
        "query_variety_ratio_ge_0_8": query_variety_ok,
        "inferred_topics_3_to_6": inferred_topics_ok,
        "topic_summary_len_ge_20": summary_ok,
    }
    score = round(sum(1 for passed in checks.values() if passed) / len(checks), 3)
    return checks, score


def run_single_evaluation(
    *,
    model_spec: str,
    transcript: str,
    locale: str,
    timeout_seconds: int,
) -> EvaluationReport:
    """Run one lane-planning evaluation call for a model."""
    prompt = _format_audio_plan_prompt(transcript, locale)
    start = time.monotonic()

    try:
        agent = get_basic_agent(model_spec, _AudioPlanOutput, AUDIO_PLAN_SYSTEM_PROMPT)
        result = agent.run_sync(prompt, model_settings={"timeout": timeout_seconds})
        raw_output = extract_output(result)
        normalized_output, used_fallback = _normalize_audio_lane_plan_with_metadata(
            raw_output, transcript
        )
        checks, score = compute_checks(normalized_output)
        duration_ms = int((time.monotonic() - start) * 1000)
        return EvaluationReport(
            model=model_spec,
            transcript=transcript,
            duration_ms=duration_ms,
            success=True,
            error=None,
            score=score,
            checks=checks,
            raw_output=raw_output.model_dump(),
            normalized_output=normalized_output.model_dump(),
            used_normalization_fallback=used_fallback,
        )
    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.monotonic() - start) * 1000)
        return EvaluationReport(
            model=model_spec,
            transcript=transcript,
            duration_ms=duration_ms,
            success=False,
            error=str(exc),
            score=0.0,
            checks={},
            raw_output=None,
            normalized_output=None,
            used_normalization_fallback=None,
        )


def render_text_report(report: EvaluationReport) -> str:
    """Render concise human-readable report block."""
    header = (
        f"model={report.model} success={report.success} "
        f"score={report.score:.3f} duration_ms={report.duration_ms}"
    )
    lines = [header]
    if report.error:
        lines.append(f"error={report.error}")
        return "\n".join(lines)

    lines.append(f"normalization_fallback={report.used_normalization_fallback}")
    for check_name, passed in report.checks.items():
        lines.append(f"check {check_name}={passed}")

    normalized = report.normalized_output or {}
    lanes = normalized.get("lanes", [])
    lines.append(f"lanes={len(lanes)} inferred_topics={len(normalized.get('inferred_topics', []))}")
    for lane in lanes:
        lines.append(
            f"- {lane['target']} | {lane['name']} | queries={len(lane.get('queries', []))}"
        )
        for query in lane.get("queries", []):
            lines.append(f"  q: {query}")
    return "\n".join(lines)


def main() -> int:
    """Run model evaluations and print results."""
    args = parse_args()
    transcripts = resolve_transcripts(args)
    models = [args.model]
    if not args.no_compare:
        models.append(args.compare_model)

    reports: list[EvaluationReport] = []
    for transcript in transcripts:
        for model_spec in models:
            for _ in range(max(args.runs, 1)):
                reports.append(
                    run_single_evaluation(
                        model_spec=model_spec,
                        transcript=transcript,
                        locale=args.locale,
                        timeout_seconds=args.timeout_seconds,
                    )
                )

    payload = [report.__dict__ for report in reports]
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    for idx, report in enumerate(reports, start=1):
        print(f"\n=== evaluation {idx}/{len(reports)} ===")
        print(f"transcript: {report.transcript}")
        print(render_text_report(report))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
