"""Tests for the production-backed Briefing layout eval harness."""

from __future__ import annotations

from typing import Any

from app.core.model_defaults import OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC
from app.services.briefing import composer
from app.services.briefing.layout_policy import (
    BriefingLayoutDisposition,
    assess_briefing_layout,
)
from scripts.support import briefing_eval


def test_production_scalar_dump_fixture_meets_expectation_but_is_invalid() -> None:
    suite = briefing_eval.load_briefing_eval_suite(briefing_eval.DEFAULT_BRIEFING_EVAL_DATASET)

    report = briefing_eval.run_briefing_eval_suite(
        suite,
        case_id="prod_segment_673_normal_scalar_dump",
    )

    result = report.results[0]
    assert result.production_segment_id == 673
    assert result.model_spec == OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC
    assert result.expectation_met is True
    assert result.raw_layout_valid is False
    assert result.layout_valid is False
    assert result.raw_assessment is not None
    assert result.raw_assessment.disposition == BriefingLayoutDisposition.RETRY
    assert result.raw_assessment.low_signal_values == ["normal"]
    assert result.final_assessment is None
    assert result.narration_text == ""
    assert result.gate_satisfied is True
    assert report.gate_satisfied is True


def test_duplicate_news_coverage_repair_fixture_is_contract_invalid() -> None:
    suite = briefing_eval.load_briefing_eval_suite(briefing_eval.DEFAULT_BRIEFING_EVAL_DATASET)

    report = briefing_eval.run_briefing_eval_suite(
        suite,
        case_id="prod_segment_1089_duplicate_news_coverage_repair",
    )

    result = report.results[0]
    assert result.production_segment_id == 1089
    assert result.expectation_met is True
    assert result.raw_assessment is not None
    assert result.raw_assessment.disposition == BriefingLayoutDisposition.REPAIR
    assert result.raw_assessment.coverage.missing_source_keys == ["news:19257", "news:19264"]
    assert result.final_assessment is not None
    assert result.final_assessment.disposition == BriefingLayoutDisposition.ACCEPT
    assert result.contract_issues == ["news_requires_one_passage"]
    assert "coverage_repair:2" in result.warnings
    assert result.layout_valid is False
    assert result.gate_satisfied is True
    assert report.gate_satisfied is True


def test_podcast_fixture_resolves_separate_llm_quote_suggestions() -> None:
    suite = briefing_eval.load_briefing_eval_suite(briefing_eval.DEFAULT_BRIEFING_EVAL_DATASET)

    report = briefing_eval.run_briefing_eval_suite(
        suite,
        case_id="prod_segment_4351_duplicate_podcast_pullquotes",
    )

    result = report.results[0]
    assert result.production_segment_id == 4351
    assert result.expectation_met is True
    assert result.raw_assessment is not None
    assert result.raw_assessment.disposition == BriefingLayoutDisposition.ACCEPT
    assert result.final_assessment is not None
    assert result.final_assessment.disposition == BriefingLayoutDisposition.ACCEPT
    assert [block["text"] for block in result.final_blocks if block["type"] == "pullquote"] == [
        "The best hiring funnel starts with a finite map of exceptional people.",
        "Hiring managers own the decision; recruiters make that decision clearer.",
        "Closing is not an offer-stage event. It runs through the entire relationship.",
    ]
    assert result.layout_valid is True
    assert result.gate_satisfied is True
    assert report.gate_satisfied is True


def test_layout_assessment_allows_normal_inside_real_source_backed_prose() -> None:
    assessment = assess_briefing_layout(
        [
            {
                "type": "passage",
                "markdown": (
                    "[Import AI](newsly://briefing/content/29560) asks how the world "
                    "stays normal as capabilities accelerate."
                ),
            }
        ],
        source_keys={"content:29560"},
    )

    assert assessment.disposition == BriefingLayoutDisposition.ACCEPT
    assert assessment.low_signal_values == []


def test_layout_assessment_treats_unknown_figure_source_as_repairable() -> None:
    assessment = assess_briefing_layout(
        [
            {
                "type": "passage",
                "markdown": (
                    "[Import AI](newsly://briefing/content/29560) explains the useful point."
                ),
            },
            {
                "type": "figure",
                "source_key": "content:999",
                "caption": "A useful contextual caption.",
            },
        ],
        source_keys={"content:29560"},
    )

    assert assessment.disposition == BriefingLayoutDisposition.REPAIR
    assert assessment.unknown_source_keys == []
    assert assessment.repairable_unknown_source_keys == ["content:999"]


def test_live_eval_runs_real_composer_repair_state_machine(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_generate(sources, **kwargs):  # noqa: ANN001
        captured["source_keys"] = [source.source_key for source in sources]
        captured.update(kwargs)
        passage = " ".join(
            f"[{source.title}](newsly://briefing/{source.kind}/{source.id}) {source.summary}"
            for source in sources
        )
        return [
            {
                "type": "passage",
                "weight": "feature",
                "markdown": passage,
            },
            {
                "type": "figure",
                "source_key": "content:999999",
                "caption": "An unsupported optional figure.",
                "placement": "inset",
            },
        ], {"input_tokens": 100, "output_tokens": 50}

    monkeypatch.setattr(composer, "generate_layout_with_llm", fake_generate)
    suite = briefing_eval.load_briefing_eval_suite(briefing_eval.DEFAULT_BRIEFING_EVAL_DATASET)

    report = briefing_eval.run_briefing_eval_suite(
        suite,
        mode="live",
        case_id="prod_segment_673_normal_scalar_dump",
        model_spec="openrouter:test/model",
    )

    result = report.results[0]
    assert result.error is None
    assert result.expectation_met is None
    assert result.raw_layout_valid is False
    assert result.layout_valid is True
    assert result.raw_assessment is not None
    assert result.raw_assessment.disposition == BriefingLayoutDisposition.REPAIR
    assert result.final_assessment is not None
    assert result.final_assessment.disposition == BriefingLayoutDisposition.ACCEPT
    assert result.result_model == "openrouter:test/model"
    assert "figure_unknown_source" in result.warnings
    assert result.usage == {"input_tokens": 100, "output_tokens": 50}
    assert captured["model_spec"] == "openrouter:test/model"
    assert captured["task_id"] is None
    assert captured["user_id"] is None
    assert captured["source_keys"] == ["content:29566", "content:29560", "content:29558"]
    assert report.gate_satisfied is True


def test_live_eval_reports_composer_retry_failure(monkeypatch) -> None:
    attempts: list[int] = []

    def malformed_generate(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        return [{"type": "passage", "markdown": "normal"}], None

    monkeypatch.setattr(composer, "generate_layout_with_llm", malformed_generate)
    suite = briefing_eval.load_briefing_eval_suite(briefing_eval.DEFAULT_BRIEFING_EVAL_DATASET)

    report = briefing_eval.run_briefing_eval_suite(
        suite,
        mode="live",
        case_id="prod_segment_673_normal_scalar_dump",
    )

    result = report.results[0]
    assert len(attempts) == composer.MAX_COMPOSE_ATTEMPTS
    assert result.error is not None
    assert "failed policy" in result.error
    assert result.layout_valid is False
    assert result.gate_satisfied is False
    assert report.gate_satisfied is False
