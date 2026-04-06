"""Fixture-driven eval harness for summary generation, with title-focused grading."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_ai import Agent

from app.services.llm_agents import get_basic_agent
from app.services.llm_models import build_pydantic_model
from app.services.llm_prompts import generate_summary_prompt
from app.services.llm_summarization import resolve_summarization_output_type

SummaryEvalContentType = Literal["article", "podcast", "news"]
LongformTemplate = Literal[
    "long_bullets_v1",
    "interleaved_v2",
    "structured_v1",
    "editorial_narrative_v1",
]
PromptType = Literal[
    "long_bullets",
    "interleaved",
    "structured",
    "news_digest",
    "editorial_narrative",
]

DEFAULT_SUMMARY_EVAL_DATASET = Path("tests") / "evals" / "summary_generation.yaml"


class SummaryEvalDefaults(BaseModel):
    """Defaults shared across all summary-generation eval cases."""

    model_spec: str = Field(default="openai:gpt-5.4", min_length=1)
    judge_model_spec: str = Field(default="openai:gpt-5.4", min_length=1)
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
    notes: str | None = Field(default=None, min_length=1)
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
        if self.notes or self.bad_titles or self.reference_titles:
            return self
        raise ValueError("Each summary eval case needs notes, bad_titles, or reference_titles")


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


def _resolve_prompt_settings(
    content_type: SummaryEvalContentType,
    *,
    longform_template: LongformTemplate,
) -> tuple[PromptType, int, int]:
    if content_type == "news":
        return "news_digest", 4, 0
    if longform_template == "interleaved_v2":
        return "interleaved", 8, 8
    if longform_template == "structured_v1":
        return "structured", 12, 8
    if longform_template == "editorial_narrative_v1":
        return "editorial_narrative", 10, 4
    return "long_bullets", 30, 3


def _build_user_message(user_template: str, content_payload: str, title: str | None) -> str:
    """Build the final user prompt for one eval call."""
    if title:
        return user_template.format(content=f"Title: {title}\n\n{content_payload}")
    return user_template.format(content=content_payload)


def _extract_result_payload(result: Any) -> dict[str, Any]:
    """Normalize pydantic-ai result output into a JSON-serializable dict."""
    output = getattr(result, "output", None)
    if output is None:
        output = getattr(result, "data", None)
    if output is None:
        raise ValueError("Model result did not include output payload")
    if hasattr(output, "model_dump"):
        return output.model_dump(mode="json", exclude_none=True)
    if isinstance(output, dict):
        return output
    raise ValueError("Model result payload is not JSON serializable")


def _run_summary_generation(
    *,
    case: SummaryEvalCase,
    model_spec: str,
    longform_template: LongformTemplate,
) -> tuple[str, dict[str, Any]]:
    """Run one live summary-generation call and return prompt type + payload."""
    prompt_type, max_bullet_points, max_quotes = _resolve_prompt_settings(
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
        resolve_summarization_output_type(prompt_type),
        system_prompt,
    )
    user_message = _build_user_message(user_template, case.input_text, case.source_title)
    result = agent.run_sync(user_message)
    return prompt_type, _extract_result_payload(result)


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
    notes = case.notes or "No extra notes."
    return (
        "You are grading a generated title for a summary-generation eval.\n\n"
        "Decide whether the generated title is grounded, specific, and materially better "
        "than the known bad titles.\n"
        "The generated title does not need to match the reference titles exactly, but it "
        "should be comparably informative and faithful.\n\n"
        f"Content type: {case.content_type}\n"
        f"Prompt type: {prompt_type}\n"
        f"Source title hint: {case.source_title or 'None'}\n"
        f"Existing title: {case.existing_title or 'None'}\n"
        f"Known bad titles:\n{bad_titles}\n\n"
        f"Reference good titles:\n{reference_titles}\n\n"
        f"Case notes:\n{notes}\n\n"
        f"Source evidence:\n{case.input_text}\n\n"
        f"Generated title:\n{generated_title}\n\n"
        f"Full generated payload:\n{payload_json}\n\n"
        "Grade on these dimensions:\n"
        "- Specificity and informativeness\n"
        "- Faithfulness to the evidence\n"
        "- Whether it avoids vague reaction-text or placeholder framing\n"
        "- Whether it captures the real takeaway or tension\n\n"
        "Fail the title if it stays generic, mirrors the bad titles, or misses the story."
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
        )
    )
    return result.output


def run_summary_eval_case(
    *,
    suite_name: str,
    defaults: SummaryEvalDefaults,
    case: SummaryEvalCase,
) -> SummaryEvalCaseResult:
    """Run one summary-generation eval case."""
    model_spec = defaults.model_spec
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
