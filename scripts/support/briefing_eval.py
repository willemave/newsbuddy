"""Fixture and live diagnostics for Briefing layout composition."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.model_defaults import OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC
from app.core.settings import Settings, get_settings
from app.models.contracts import ContentType
from app.services.briefing.composer import (
    compose_window,
    news_layout_contract_issues,
    process_generated_layout,
)
from app.services.briefing.layout_policy import (
    BriefingLayoutAssessment,
    BriefingLayoutDisposition,
    normalize_low_signal_value,
)
from app.services.briefing.sources import BriefingSource

DEFAULT_BRIEFING_EVAL_DATASET = (
    Path(__file__).resolve().parents[2] / "tests" / "evals" / "briefing_layout.yaml"
)
BriefingEvalMode = Literal["fixture", "live"]


class BriefingEvalSource(BaseModel):
    """Serializable Briefing source used by fixture and live eval lanes."""

    source_key: str = Field(min_length=1)
    kind: Literal["content", "news"]
    id: int = Field(ge=1)
    tier: Literal["audio", "longform", "news"]
    lens_key: str | None = None
    title: str = Field(min_length=1)
    summary: str | None = None
    key_points: list[str] = Field(default_factory=list)
    url: str | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None
    published_at: datetime | None = None
    content_type: ContentType | None = None
    briefing_context: str | None = None

    model_config = ConfigDict(extra="forbid")

    def to_briefing_source(self) -> BriefingSource:
        return BriefingSource(**self.model_dump())


class BriefingEvalCase(BaseModel):
    """One production-backed layout fixture and its expected policy decision."""

    id: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    production_segment_id: int | None = Field(default=None, ge=1)
    lens_key: str = Field(min_length=1)
    lens_title: str = Field(min_length=1)
    tier: Literal["audio", "longform", "news"]
    window_index: int = Field(default=0, ge=0)
    sources: list[BriefingEvalSource] = Field(min_length=1)
    fixture_blocks: list[dict[str, Any]] = Field(min_length=1)
    expected_disposition: BriefingLayoutDisposition
    expected_low_signal_values: list[str] = Field(default_factory=list)
    expected_contract_issues: list[str] = Field(default_factory=list)
    expected_warnings: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator("expected_low_signal_values")
    @classmethod
    def normalize_expected_values(cls, values: list[str]) -> list[str]:
        normalized = [normalize_low_signal_value(value) for value in values]
        return list(dict.fromkeys(value for value in normalized if value))

    @model_validator(mode="after")
    def validate_unique_source_keys(self) -> BriefingEvalCase:
        source_keys = [source.source_key for source in self.sources]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError(f"Duplicate source key in eval case: {self.id}")
        return self


class BriefingEvalSuite(BaseModel):
    """YAML-backed collection of Briefing layout evals."""

    suite: str = Field(min_length=1, max_length=200)
    cases: list[BriefingEvalCase] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @field_validator("cases")
    @classmethod
    def validate_unique_case_ids(cls, cases: list[BriefingEvalCase]) -> list[BriefingEvalCase]:
        case_ids = [case.id for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Briefing eval case IDs must be unique")
        return cases


class BriefingEvalCaseResult(BaseModel):
    """Observed policy and expectation results for one eval case."""

    suite: str
    case_id: str
    mode: BriefingEvalMode
    model_spec: str
    production_segment_id: int | None = None
    result_model: str | None = None
    expectation_met: bool | None = None
    raw_layout_valid: bool | None = None
    layout_valid: bool
    raw_assessment: BriefingLayoutAssessment | None = None
    final_assessment: BriefingLayoutAssessment | None = None
    contract_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usage: dict[str, int | None] | None = None
    raw_blocks: list[dict[str, Any]] = Field(default_factory=list)
    final_blocks: list[dict[str, Any]] = Field(default_factory=list)
    narration_text: str = ""
    generation_attempts: int = 0
    error: str | None = None

    @property
    def gate_satisfied(self) -> bool:
        if self.error is not None:
            return False
        if self.mode == "fixture":
            return self.expectation_met is True
        return self.layout_valid


class BriefingEvalReport(BaseModel):
    suite: str
    mode: BriefingEvalMode
    results: list[BriefingEvalCaseResult]

    @property
    def gate_satisfied(self) -> bool:
        return all(result.gate_satisfied for result in self.results)


def load_briefing_eval_suite(path: str | Path) -> BriefingEvalSuite:
    """Load and validate a Briefing eval suite from YAML."""
    with open(path, encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return BriefingEvalSuite.model_validate(payload)


def run_briefing_eval_case(
    *,
    suite_name: str,
    case: BriefingEvalCase,
    mode: BriefingEvalMode,
    model_spec: str,
    settings: Settings,
) -> BriefingEvalCaseResult:
    """Run one frozen policy fixture or the real production composition state machine."""
    sources = [source.to_briefing_source() for source in case.sources]
    try:
        if mode == "live":
            live_settings = settings.model_copy(update={"briefing_model": model_spec})
            segment = compose_window(
                sources,
                lens_key=case.lens_key,
                lens_title=case.lens_title,
                tier=case.tier,
                window_index=case.window_index,
                use_llm=True,
                settings=live_settings,
            )
            usage = None
            if segment.input_tokens is not None or segment.output_tokens is not None:
                usage = {
                    "input_tokens": segment.input_tokens,
                    "output_tokens": segment.output_tokens,
                }
            raw_assessment = segment.raw_assessment
            final_assessment = segment.final_assessment
            return BriefingEvalCaseResult(
                suite=suite_name,
                case_id=case.id,
                mode=mode,
                model_spec=model_spec,
                production_segment_id=case.production_segment_id,
                result_model=segment.model,
                raw_layout_valid=(
                    raw_assessment.layout_valid if raw_assessment is not None else None
                ),
                layout_valid=(
                    final_assessment.layout_valid if final_assessment is not None else False
                ),
                raw_assessment=raw_assessment,
                final_assessment=final_assessment,
                warnings=segment.warnings,
                usage=usage,
                raw_blocks=segment.raw_blocks or [],
                final_blocks=segment.blocks,
                narration_text=segment.narration_text,
                generation_attempts=segment.generation_attempts,
            )

        figure_budget = (
            settings.briefing_max_figures_news
            if case.tier == "news"
            else settings.briefing_max_figures_deep
        )
        processed = process_generated_layout(
            [dict(block) for block in case.fixture_blocks],
            sources=sources,
            lens_key=case.lens_key,
            window_index=case.window_index,
            figure_budget=figure_budget,
            ensure_source_figures=case.tier != "news",
        )
        contract_issues = (
            news_layout_contract_issues(processed, sources=sources) if case.tier == "news" else []
        )
        expected_low_signal_values = set(case.expected_low_signal_values)
        expectation_met = (
            processed.raw_assessment.disposition == case.expected_disposition
            and expected_low_signal_values.issubset(set(processed.raw_assessment.low_signal_values))
            and contract_issues == case.expected_contract_issues
            and set(case.expected_warnings).issubset(set(processed.warnings))
        )
        return BriefingEvalCaseResult(
            suite=suite_name,
            case_id=case.id,
            mode=mode,
            model_spec=model_spec,
            production_segment_id=case.production_segment_id,
            expectation_met=expectation_met,
            raw_layout_valid=processed.raw_assessment.layout_valid,
            layout_valid=processed.accepted and not contract_issues,
            raw_assessment=processed.raw_assessment,
            final_assessment=processed.final_assessment,
            contract_issues=contract_issues,
            warnings=processed.warnings,
            raw_blocks=processed.raw_blocks,
            final_blocks=(processed.normalized.blocks if processed.normalized is not None else []),
            narration_text=(
                processed.normalized.narration_text if processed.normalized is not None else ""
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return BriefingEvalCaseResult(
            suite=suite_name,
            case_id=case.id,
            mode=mode,
            model_spec=model_spec,
            production_segment_id=case.production_segment_id,
            layout_valid=False,
            error=str(exc),
        )


def run_briefing_eval_suite(
    suite: BriefingEvalSuite,
    *,
    mode: BriefingEvalMode = "fixture",
    case_id: str | None = None,
    model_spec: str = OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC,
    settings: Settings | None = None,
) -> BriefingEvalReport:
    """Run selected Briefing eval cases in fixture or live mode."""
    selected_cases = suite.cases
    if case_id:
        selected_cases = [case for case in suite.cases if case.id == case_id]
        if not selected_cases:
            raise ValueError(f"Unknown eval case: {case_id}")
    settings = settings or get_settings()
    return BriefingEvalReport(
        suite=suite.suite,
        mode=mode,
        results=[
            run_briefing_eval_case(
                suite_name=suite.suite,
                case=case,
                mode=mode,
                model_spec=model_spec,
                settings=settings,
            )
            for case in selected_cases
        ],
    )
