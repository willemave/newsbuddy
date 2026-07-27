"""Fixture-driven eval harness for summary generation, with title-focused grading."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_ai import Agent

from app.core.model_defaults import SMART_ANTHROPIC_MODEL_SPEC
from app.services.eval_common import extract_result_payload, resolve_summary_prompt_settings
from app.services.llm_agents import get_basic_agent
from app.services.llm_models import build_pydantic_model
from app.services.llm_prompts import generate_summary_prompt
from app.services.llm_summarization import (
    SummarizationPromptType,
    resolve_summarization_output_type,
    resolve_summarization_spec,
)
from app.services.prompt_library import render_prompt

SummaryEvalContentType = Literal["article", "podcast", "news"]
LongformTemplate = Literal[
    "long_bullets_v1",
    "interleaved_v2",
    "structured_v1",
    "editorial_narrative_v1",
]
DEFAULT_SUMMARY_EVAL_DATASET = Path("tests") / "evals" / "summary_generation.yaml"
SUMMARY_EVAL_CALL_TIMEOUT_SECONDS = 120.0


class SummaryEvalDefaults(BaseModel):
    """Defaults shared across all summary-generation eval cases."""

    model_spec: str | None = Field(default=None, min_length=1)
    judge_model_spec: str = Field(
        default=SMART_ANTHROPIC_MODEL_SPEC,
        min_length=1,
    )
    longform_template: LongformTemplate = "editorial_narrative_v1"


class SummaryEvalCase(BaseModel):
    """One fixture-backed summary-generation eval case."""

    id: str = Field(..., min_length=1, max_length=200)
    content_type: SummaryEvalContentType
    input_text: str = Field(..., min_length=1)
    source_title: str | None = Field(default=None, min_length=1, max_length=500)
    existing_title: str | None = Field(default=None, min_length=1, max_length=500)
    bad_titles: list[str] = Field(default_factory=list)
    reference_titles: list[str] = Field(default_factory=list)
    evaluation_criteria: str | None = Field(default=None, min_length=1)
    source_id: int | None = Field(default=None, ge=1)
    source_kind: Literal["content", "news_item"] | None = None
    source_url: str | None = Field(default=None, min_length=1, max_length=2048)

    model_config = ConfigDict(extra="forbid")

    @field_validator("bad_titles", "reference_titles")
    @classmethod
    def validate_titles(cls, value: list[str]) -> list[str]:
        """Deduplicate titles while preserving order."""
        normalized = [entry.strip() for entry in value if entry and entry.strip()]
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_case_has_eval_guidance(self) -> SummaryEvalCase:
        """Ensure each case contains enough guidance for title grading."""
        if self.evaluation_criteria or self.bad_titles or self.reference_titles:
            return self
        raise ValueError(
            "Each summary eval case needs evaluation_criteria, bad_titles, or reference_titles"
        )


class SummaryEvalSuite(BaseModel):
    """YAML-backed suite of summary-generation eval cases."""

    suite: str = Field(..., min_length=1, max_length=200)
    defaults: SummaryEvalDefaults = Field(default_factory=SummaryEvalDefaults)
    cases: list[SummaryEvalCase]

    model_config = ConfigDict(extra="forbid")

    @field_validator("cases")
    @classmethod
    def validate_cases(cls, value: list[SummaryEvalCase]) -> list[SummaryEvalCase]:
        """Ensure the suite contains unique case IDs."""
        seen: set[str] = set()
        for case in value:
            if case.id in seen:
                raise ValueError(f"Duplicate case id: {case.id}")
            seen.add(case.id)
        return value


class TitleJudgeVerdict(BaseModel):
    """Structured judge verdict for one generated title."""

    passed: bool
    score: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., min_length=1)


class SummaryEvalCaseResult(BaseModel):
    """Result for one summary-generation eval case."""

    suite: str
    case_id: str
    content_type: SummaryEvalContentType
    model_spec: str
    judge_model_spec: str
    prompt_type: str
    source_id: int | None = None
    source_kind: str | None = None
    source_url: str | None = None
    existing_title: str | None = None
    generated_title: str | None = None
    bad_titles: list[str] = Field(default_factory=list)
    reference_titles: list[str] = Field(default_factory=list)
    passed: bool
    score: float | None = None
    reasoning: str | None = None
    raw_output: dict[str, Any] | None = None
    error: str | None = None


class SummaryEvalReport(BaseModel):
    """Whole-run report for summary-generation evals."""

    suite: str
    results: list[SummaryEvalCaseResult]


def load_summary_eval_suite(path: str | Path) -> SummaryEvalSuite:
    """Load a summary-generation eval suite from YAML."""
    with open(path, encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return SummaryEvalSuite.model_validate(payload)


def _build_user_message(user_template: str, content_payload: str, title: str | None) -> str:
    """Build the final user prompt for one eval call."""
    if title:
        return user_template.format(content=f"Title: {title}\n\n{content_payload}")
    return user_template.format(content=content_payload)


def _resolve_generation_model_spec(
    *,
    case: SummaryEvalCase,
    defaults: SummaryEvalDefaults,
) -> str:
    """Resolve the generation model spec using real app routing by default."""
    if defaults.model_spec:
        return defaults.model_spec

    if case.content_type == "news":
        return resolve_summarization_spec(case.content_type)[2]

    if defaults.longform_template == "editorial_narrative_v1":
        return resolve_summarization_spec(case.content_type)[2]

    prompt_type, _, _ = resolve_summary_prompt_settings(
        case.content_type,
        longform_template=defaults.longform_template,
    )
    return resolve_summarization_spec(prompt_type)[2]


def _run_summary_generation(
    *,
    case: SummaryEvalCase,
    model_spec: str,
    longform_template: LongformTemplate,
) -> tuple[str, dict[str, Any]]:
    """Run one live summary-generation call and return prompt type + payload."""
    prompt_type, max_bullet_points, max_quotes = resolve_summary_prompt_settings(
        case.content_type,
        longform_template=longform_template,
    )
    system_prompt, user_template = generate_summary_prompt(
        prompt_type,
        max_bullet_points=max_bullet_points,
        max_quotes=max_quotes,
    )
    agent = get_basic_agent(
        model_spec,
        resolve_summarization_output_type(cast(SummarizationPromptType, prompt_type)),
        system_prompt,
    )
    user_message = _build_user_message(user_template, case.input_text, case.source_title)
    result = agent.run_sync(
        user_message,
        model_settings={"timeout": SUMMARY_EVAL_CALL_TIMEOUT_SECONDS},
    )
    return prompt_type, extract_result_payload(result)


def build_title_judge_prompt(
    *,
    case: SummaryEvalCase,
    prompt_type: str,
    generated_title: str,
    raw_output: dict[str, Any],
) -> str:
    """Build the judge prompt for one title eval case."""
    payload_json = json.dumps(raw_output, indent=2, sort_keys=True, ensure_ascii=False)
    bad_titles = "\n".join(f"- {title}" for title in case.bad_titles) or "- None provided"
    reference_titles = (
        "\n".join(f"- {title}" for title in case.reference_titles) or "- None provided"
    )
    evaluation_criteria = case.evaluation_criteria or "No extra evaluation criteria."
    return render_prompt(
        "evals/judges#title_judge_user",
        content_type=case.content_type,
        prompt_type=prompt_type,
        source_title=case.source_title or "None",
        existing_title=case.existing_title or "None",
        bad_titles=bad_titles,
        reference_titles=reference_titles,
        evaluation_criteria=evaluation_criteria,
        input_text=case.input_text,
        generated_title=generated_title,
        payload_json=payload_json,
    )


def judge_generated_title(
    *,
    case: SummaryEvalCase,
    prompt_type: str,
    generated_title: str,
    raw_output: dict[str, Any],
    judge_model_spec: str,
) -> TitleJudgeVerdict:
    """Judge one generated title against the case guidance."""
    model, model_settings = build_pydantic_model(judge_model_spec)
    judge_agent: Agent[None, TitleJudgeVerdict] = Agent(
        model,
        output_type=TitleJudgeVerdict,
        model_settings=model_settings,
    )
    result = judge_agent.run_sync(
        build_title_judge_prompt(
            case=case,
            prompt_type=prompt_type,
            generated_title=generated_title,
            raw_output=raw_output,
        ),
        model_settings={"timeout": SUMMARY_EVAL_CALL_TIMEOUT_SECONDS},
    )
    return result.output


def run_summary_eval_case(
    *,
    suite_name: str,
    defaults: SummaryEvalDefaults,
    case: SummaryEvalCase,
) -> SummaryEvalCaseResult:
    """Run one summary-generation eval case."""
    model_spec = _resolve_generation_model_spec(case=case, defaults=defaults)
    judge_model_spec = defaults.judge_model_spec

    try:
        prompt_type, raw_output = _run_summary_generation(
            case=case,
            model_spec=model_spec,
            longform_template=defaults.longform_template,
        )
        generated_title = raw_output.get("title")
        if not isinstance(generated_title, str) or not generated_title.strip():
            return SummaryEvalCaseResult(
                suite=suite_name,
                case_id=case.id,
                content_type=case.content_type,
                model_spec=model_spec,
                judge_model_spec=judge_model_spec,
                prompt_type=prompt_type,
                source_id=case.source_id,
                source_kind=case.source_kind,
                source_url=case.source_url,
                existing_title=case.existing_title,
                bad_titles=case.bad_titles,
                reference_titles=case.reference_titles,
                passed=False,
                score=0.0,
                reasoning="Generated payload did not include a non-empty title.",
                raw_output=raw_output,
            )

        normalized_title = generated_title.strip()
        bad_titles_lower = {title.casefold() for title in case.bad_titles}
        if normalized_title.casefold() in bad_titles_lower:
            return SummaryEvalCaseResult(
                suite=suite_name,
                case_id=case.id,
                content_type=case.content_type,
                model_spec=model_spec,
                judge_model_spec=judge_model_spec,
                prompt_type=prompt_type,
                source_id=case.source_id,
                source_kind=case.source_kind,
                source_url=case.source_url,
                existing_title=case.existing_title,
                generated_title=normalized_title,
                bad_titles=case.bad_titles,
                reference_titles=case.reference_titles,
                passed=False,
                score=0.0,
                reasoning="Generated title matched a known bad title exactly.",
                raw_output=raw_output,
            )

        verdict = judge_generated_title(
            case=case,
            prompt_type=prompt_type,
            generated_title=normalized_title,
            raw_output=raw_output,
            judge_model_spec=judge_model_spec,
        )
        return SummaryEvalCaseResult(
            suite=suite_name,
            case_id=case.id,
            content_type=case.content_type,
            model_spec=model_spec,
            judge_model_spec=judge_model_spec,
            prompt_type=prompt_type,
            source_id=case.source_id,
            source_kind=case.source_kind,
            source_url=case.source_url,
            existing_title=case.existing_title,
            generated_title=normalized_title,
            bad_titles=case.bad_titles,
            reference_titles=case.reference_titles,
            passed=verdict.passed,
            score=verdict.score,
            reasoning=verdict.reasoning,
            raw_output=raw_output,
        )
    except Exception as exc:  # noqa: BLE001
        return SummaryEvalCaseResult(
            suite=suite_name,
            case_id=case.id,
            content_type=case.content_type,
            model_spec=model_spec,
            judge_model_spec=judge_model_spec,
            prompt_type="unknown",
            source_id=case.source_id,
            source_kind=case.source_kind,
            source_url=case.source_url,
            existing_title=case.existing_title,
            bad_titles=case.bad_titles,
            reference_titles=case.reference_titles,
            passed=False,
            error=str(exc),
        )


def run_summary_eval_suite(
    suite: SummaryEvalSuite,
    *,
    case_id: str | None = None,
    model_spec: str | None = None,
    judge_model_spec: str | None = None,
) -> SummaryEvalReport:
    """Run all requested cases in a summary-generation eval suite."""
    defaults = suite.defaults.model_copy(deep=True)
    if model_spec:
        defaults.model_spec = model_spec
    if judge_model_spec:
        defaults.judge_model_spec = judge_model_spec

    selected_cases = suite.cases
    if case_id:
        selected_cases = [case for case in suite.cases if case.id == case_id]
        if not selected_cases:
            raise ValueError(f"Unknown eval case: {case_id}")

    results = [
        run_summary_eval_case(
            suite_name=suite.suite,
            defaults=defaults,
            case=case,
        )
        for case in selected_cases
    ]
    return SummaryEvalReport(suite=suite.suite, results=results)
