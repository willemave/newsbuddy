"""Evaluation helpers for onboarding audio-plan structured tool calls."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai import Agent, ToolOutput
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.settings import ModelSettings

from app.services.llm_models import build_pydantic_model
from app.services.onboarding.audio_plan_heuristics import (
    _normalize_audio_lane_plan_with_metadata,
)
from app.services.onboarding.config import AUDIO_PLAN_SYSTEM_PROMPT
from app.services.onboarding.internal_models import _AudioPlanOutput
from app.services.onboarding.llm_plans import _format_audio_plan_prompt

DEEPSEEK_V4_FLASH_0731 = "openrouter:deepseek/deepseek-v4-flash-0731"
DEFAULT_JUDGE_MODEL = "gpt-5.6-sol"
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_CODEX_TIMEOUT_SECONDS = 120
ONBOARDING_JUDGE_PASS_SCORE = 0.7


@dataclass(frozen=True)
class OnboardingEvalCandidate:
    """One model/provider route evaluated against the production schema."""

    alias: str
    model_spec: str
    provider_tag: str | None = None
    reasoning_effort: str | None = None


ONBOARDING_EVAL_CANDIDATES: dict[str, OnboardingEvalCandidate] = {
    candidate.alias: candidate
    for candidate in (
        OnboardingEvalCandidate(
            "kimi_deepinfra",
            "openrouter:moonshotai/kimi-k2.6",
            "deepinfra/fp4",
        ),
        OnboardingEvalCandidate(
            "glm_5_3_fireworks_low",
            "openrouter:z-ai/glm-5.3",
            "fireworks",
            "low",
        ),
        OnboardingEvalCandidate(
            "glm_5_3_baseten_low",
            "openrouter:z-ai/glm-5.3",
            "baseten/fp4",
            "low",
        ),
        OnboardingEvalCandidate(
            "glm_5_3_flash_deepinfra_low",
            "openrouter:z-ai/glm-5.3-flash",
            "deepinfra/fp8",
            "low",
        ),
        OnboardingEvalCandidate("deepseek_coreweave", DEEPSEEK_V4_FLASH_0731, "coreweave/fp8"),
        OnboardingEvalCandidate("deepseek_baseten", DEEPSEEK_V4_FLASH_0731, "baseten/fp8"),
        OnboardingEvalCandidate("deepseek_wafer", DEEPSEEK_V4_FLASH_0731, "wafer/fast"),
        OnboardingEvalCandidate("deepseek_reka", DEEPSEEK_V4_FLASH_0731, "reka/fp4"),
    )
}
DEFAULT_ONBOARDING_EVAL_CANDIDATE_ALIASES = (
    "kimi_deepinfra",
    "deepseek_coreweave",
    "deepseek_baseten",
    "deepseek_wafer",
    "deepseek_reka",
)


class OnboardingAudioPlanEvalCase(BaseModel):
    """One spoken onboarding request to evaluate."""

    id: str = Field(..., min_length=1, max_length=200)
    transcript: str = Field(..., min_length=1)
    locale: str = Field(default="en-US", min_length=1)


class OnboardingAudioPlanEvalSuite(BaseModel):
    """YAML-backed collection of onboarding audio-plan cases."""

    suite: str = Field(..., min_length=1, max_length=200)
    cases: list[OnboardingAudioPlanEvalCase]

    model_config = ConfigDict(extra="forbid")

    @field_validator("cases")
    @classmethod
    def validate_cases(
        cls, value: list[OnboardingAudioPlanEvalCase]
    ) -> list[OnboardingAudioPlanEvalCase]:
        """Require at least one case and unique case IDs."""
        if not value:
            raise ValueError("At least one onboarding eval case is required")
        seen: set[str] = set()
        for case in value:
            if case.id in seen:
                raise ValueError(f"Duplicate case id: {case.id}")
            seen.add(case.id)
        return value


class OnboardingAudioPlanJudgeVerdict(BaseModel):
    """Semantic verdict for one validated candidate tool call."""

    passed: bool
    score: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., min_length=1)


class _OnboardingAudioPlanJudgeScore(BaseModel):
    """Model-provided quality score before deterministic thresholding."""

    score: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., min_length=1)


class OnboardingAudioPlanEvalResult(BaseModel):
    """Measured result for one candidate, case, and repetition."""

    case_id: str
    candidate_alias: str
    model_spec: str
    provider_tag: str | None = None
    repetition: int
    duration_ms: int
    tool_call_valid: bool
    tool_call_observed: bool
    mechanical_score: float
    checks: dict[str, bool] = Field(default_factory=dict)
    used_normalization_fallback: bool | None = None
    output: dict[str, Any] | None = None
    judge: OnboardingAudioPlanJudgeVerdict | None = None
    error: str | None = None


class OnboardingAudioPlanEvalReport(BaseModel):
    """Complete report for one onboarding tool-call comparison."""

    suite: str
    judge_model: str | None
    results: list[OnboardingAudioPlanEvalResult]


def load_onboarding_audio_plan_eval_suite(
    path: str | Path,
) -> OnboardingAudioPlanEvalSuite:
    """Load and validate an onboarding eval YAML file."""
    with open(path, encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return OnboardingAudioPlanEvalSuite.model_validate(payload)


def resolve_onboarding_eval_candidates(aliases: list[str]) -> list[OnboardingEvalCandidate]:
    """Resolve candidate aliases while preserving the requested order."""
    unknown = [alias for alias in aliases if alias not in ONBOARDING_EVAL_CANDIDATES]
    if unknown:
        raise ValueError(f"Unknown onboarding eval candidates: {', '.join(unknown)}")
    return [ONBOARDING_EVAL_CANDIDATES[alias] for alias in dict.fromkeys(aliases)]


def build_candidate_model_settings(
    candidate: OnboardingEvalCandidate,
    base_settings: ModelSettings | None,
    *,
    timeout_seconds: int,
) -> ModelSettings:
    """Build fail-closed settings for a specific candidate route."""
    settings = dict(base_settings or {})
    settings["timeout"] = timeout_seconds
    if candidate.provider_tag:
        settings["openrouter_provider"] = {
            "order": [candidate.provider_tag],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
            "zdr": True,
        }
        settings["openrouter_reasoning"] = (
            {"effort": candidate.reasoning_effort, "exclude": True}
            if candidate.reasoning_effort
            else {"enabled": False, "exclude": True}
        )
    return cast(ModelSettings, settings)


def compute_audio_plan_checks(plan: _AudioPlanOutput) -> tuple[dict[str, bool], float]:
    """Score deterministic requirements from the production onboarding prompt."""
    lanes = list(plan.lanes)
    queries = [query for lane in lanes for query in lane.queries]
    normalized_queries = {query.strip().casefold() for query in queries if query.strip()}
    checks = {
        "lane_count_3_to_5": 3 <= len(lanes) <= 5,
        "inferred_topics_3_to_6": 3 <= len(plan.inferred_topics) <= 6,
        "has_reddit_lane": any(lane.target == "reddit" for lane in lanes),
        "queries_per_lane_2_to_4": bool(lanes)
        and all(2 <= len(lane.queries) <= 4 for lane in lanes),
        "query_word_count_5_to_10": bool(queries)
        and all(5 <= len(query.split()) <= 10 for query in queries),
        "queries_unique": len(normalized_queries) == len(queries),
        "topic_summary_present": bool(plan.topic_summary.strip()),
    }
    score = sum(checks.values()) / len(checks)
    return checks, round(score, 3)


def _tool_call_was_observed(result: Any) -> bool:
    """Return whether the successful result came from a model tool call."""
    for message in result.all_messages():
        if isinstance(message, ModelResponse) and any(
            isinstance(part, ToolCallPart) for part in message.parts
        ):
            return True
    return False


def run_onboarding_audio_plan_candidate(
    *,
    case: OnboardingAudioPlanEvalCase,
    candidate: OnboardingEvalCandidate,
    repetition: int,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> OnboardingAudioPlanEvalResult:
    """Run one strict tool-output call and collect latency and correctness."""
    started_at = time.perf_counter()
    try:
        model, base_settings = build_pydantic_model(candidate.model_spec)
        agent: Agent[None, _AudioPlanOutput] = Agent(
            model,
            output_type=ToolOutput(_AudioPlanOutput, strict=True),
            system_prompt=AUDIO_PLAN_SYSTEM_PROMPT,
            model_settings=build_candidate_model_settings(
                candidate,
                base_settings,
                timeout_seconds=timeout_seconds,
            ),
            retries={"output": 1},
        )
        result = agent.run_sync(_format_audio_plan_prompt(case.transcript, case.locale))
        normalized, used_fallback = _normalize_audio_lane_plan_with_metadata(
            result.output, case.transcript
        )
        checks, mechanical_score = compute_audio_plan_checks(normalized)
        return OnboardingAudioPlanEvalResult(
            case_id=case.id,
            candidate_alias=candidate.alias,
            model_spec=candidate.model_spec,
            provider_tag=candidate.provider_tag,
            repetition=repetition,
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            tool_call_valid=True,
            tool_call_observed=_tool_call_was_observed(result),
            mechanical_score=mechanical_score,
            checks=checks,
            used_normalization_fallback=used_fallback,
            output=normalized.model_dump(mode="json"),
        )
    except Exception as exc:  # noqa: BLE001
        return OnboardingAudioPlanEvalResult(
            case_id=case.id,
            candidate_alias=candidate.alias,
            model_spec=candidate.model_spec,
            provider_tag=candidate.provider_tag,
            repetition=repetition,
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            tool_call_valid=False,
            tool_call_observed=False,
            mechanical_score=0.0,
            error=str(exc),
        )


def _run_codex_structured[OutputT: BaseModel](
    prompt: str,
    *,
    output_type: type[OutputT],
    model: str,
    timeout_seconds: int,
) -> OutputT:
    """Run one ephemeral, schema-constrained Codex CLI judgment."""
    with tempfile.TemporaryDirectory(prefix="newsly-onboarding-eval-") as temp_dir:
        temp_path = Path(temp_dir)
        schema_path = temp_path / "schema.json"
        output_path = temp_path / "output.json"
        schema = output_type.model_json_schema()
        _normalize_strict_json_schema(schema)
        schema_path.write_text(
            json.dumps(schema),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                "codex",
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--model",
                model,
                "--config",
                'model_reasoning_effort="high"',
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ],
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"Codex judge failed: {detail}")
        return output_type.model_validate_json(output_path.read_text(encoding="utf-8"))


def _normalize_strict_json_schema(value: Any) -> None:
    """Make a Pydantic schema acceptable to Codex strict structured output."""
    if isinstance(value, dict):
        if value.get("type") == "object" or "properties" in value:
            properties = value.get("properties", {})
            value["additionalProperties"] = False
            value["required"] = list(properties)
        for child in value.values():
            _normalize_strict_json_schema(child)
    elif isinstance(value, list):
        for child in value:
            _normalize_strict_json_schema(child)


def build_audio_plan_judge_prompt(
    *,
    case: OnboardingAudioPlanEvalCase,
    candidate_output: dict[str, Any],
) -> str:
    """Build a simple perceived link-quality judge prompt."""
    return (
        "Judge only the perceived quality of the links these searches are likely to discover for "
        "the user's narration. Consider: (1) relevance to what the user said, without meaningful "
        "topic drift; (2) diversity across useful sources, perspectives, and formats rather than "
        "repetitive results; and (3) whether the queries are practical search phrases likely to "
        "find worthwhile feeds, podcasts, or Reddit discussions. Ignore minor wording and "
        "formatting issues unless they would materially worsen the discovered links. Score "
        "0.90-1.00 for an "
        "excellent plan, 0.70-0.89 for a good plan, 0.50-0.69 for a mixed plan, and below 0.50 for "
        "a poor plan. Explain the most important strength and weakness briefly.\n\n"
        f"user narration:\n{case.transcript}\n\n"
        f"candidate plan:\n{json.dumps(candidate_output, indent=2)}"
    )


def judge_onboarding_audio_plan(
    *,
    case: OnboardingAudioPlanEvalCase,
    candidate_output: dict[str, Any],
    judge_model: str = DEFAULT_JUDGE_MODEL,
    timeout_seconds: int = DEFAULT_CODEX_TIMEOUT_SECONDS,
) -> OnboardingAudioPlanJudgeVerdict:
    """Use the locally authenticated smarter model to score one candidate output."""
    score = _run_codex_structured(
        build_audio_plan_judge_prompt(
            case=case,
            candidate_output=candidate_output,
        ),
        output_type=_OnboardingAudioPlanJudgeScore,
        model=judge_model,
        timeout_seconds=timeout_seconds,
    )
    return OnboardingAudioPlanJudgeVerdict(
        passed=score.score >= ONBOARDING_JUDGE_PASS_SCORE,
        score=score.score,
        reasoning=score.reasoning,
    )


def run_onboarding_audio_plan_eval_suite(
    suite: OnboardingAudioPlanEvalSuite,
    *,
    candidates: list[OnboardingEvalCandidate],
    runs: int = 1,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    judge: bool = True,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    codex_timeout_seconds: int = DEFAULT_CODEX_TIMEOUT_SECONDS,
) -> OnboardingAudioPlanEvalReport:
    """Run candidate calls and optional perceived-quality judging."""
    if runs < 1:
        raise ValueError("runs must be at least 1")

    results: list[OnboardingAudioPlanEvalResult] = []
    for case in suite.cases:
        for candidate in candidates:
            for repetition in range(1, runs + 1):
                result = run_onboarding_audio_plan_candidate(
                    case=case,
                    candidate=candidate,
                    repetition=repetition,
                    timeout_seconds=timeout_seconds,
                )
                if judge and result.output is not None:
                    result.judge = judge_onboarding_audio_plan(
                        case=case,
                        candidate_output=result.output,
                        judge_model=judge_model,
                        timeout_seconds=codex_timeout_seconds,
                    )
                results.append(result)

    return OnboardingAudioPlanEvalReport(
        suite=suite.suite,
        judge_model=judge_model if judge else None,
        results=results,
    )


def estimate_eval_call_count(
    *, case_count: int, candidate_count: int, runs: int, judge: bool
) -> int:
    """Return the exact number of live model calls the eval will make."""
    candidate_calls = case_count * candidate_count * runs
    judge_calls = candidate_calls if judge else 0
    return candidate_calls + judge_calls
