"""Tests for the fixture-backed summary generation eval harness."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import summary_eval
from app.services.eval_common import extract_result_payload, extract_result_usage


def test_load_summary_eval_suite_parses_yaml(tmp_path: Path) -> None:
    """Summary eval suite loader should parse defaults and cases."""
    dataset = tmp_path / "summary_generation.yaml"
    dataset.write_text(
        "\n".join(
            [
                "suite: summary_generation_v1",
                "defaults:",
                "  judge_model_spec: anthropic:claude-opus-4-6",
                "  longform_template: editorial_narrative_v1",
                "cases:",
                "  - id: case-1",
                "    content_type: news",
                "    source_title: wow",
                "    existing_title: wow",
                "    bad_titles:",
                "      - wow",
                "    reference_titles:",
                "      - A specific title about the underlying announcement",
                "    evaluation_criteria: Replace reaction text with the actual takeaway.",
                "    input_text: >",
                "      The source announces a concrete product launch and why it matters.",
            ]
        ),
        encoding="utf-8",
    )

    suite = summary_eval.load_summary_eval_suite(dataset)

    assert suite.suite == "summary_generation_v1"
    assert suite.defaults.model_spec is None
    assert suite.defaults.judge_model_spec == "anthropic:claude-opus-4-6"
    assert suite.cases[0].id == "case-1"
    assert suite.cases[0].bad_titles == ["wow"]


def test_extract_result_payload_rejects_legacy_data_fallback() -> None:
    class FakeLegacyResult:
        data = {"title": "legacy"}

    with pytest.raises(ValueError, match="output payload"):
        extract_result_payload(FakeLegacyResult())


def test_extract_result_usage_normalizes_current_usage_property() -> None:
    result = SimpleNamespace(
        usage=SimpleNamespace(input_tokens="12", output_tokens=8, total_tokens=None)
    )

    assert extract_result_usage(result) == {
        "input_tokens": 12,
        "output_tokens": 8,
        "total_tokens": 20,
    }


def test_run_summary_eval_case_fails_when_generated_title_matches_bad_title(
    monkeypatch,
) -> None:
    """Runner should hard-fail exact matches against known bad titles."""

    def fake_run_summary_generation(*, case, model_spec, longform_template):  # noqa: ANN001
        del model_spec, longform_template
        return "news", {"title": case.bad_titles[0], "summary": "ignored"}

    monkeypatch.setattr(summary_eval, "_run_summary_generation", fake_run_summary_generation)

    result = summary_eval.run_summary_eval_case(
        suite_name="summary_generation_v1",
        defaults=summary_eval.SummaryEvalDefaults(),
        case=summary_eval.SummaryEvalCase(
            id="bad-match",
            content_type="news",
            input_text="Concrete evidence",
            existing_title="wow",
            bad_titles=["wow"],
            reference_titles=["Concrete title about the event"],
            evaluation_criteria="Bad titles are generic reactions.",
        ),
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.generated_title == "wow"
    assert result.reasoning == "Generated title matched a known bad title exactly."


def test_run_summary_eval_case_uses_judge_verdict(monkeypatch) -> None:
    """Runner should return judge output for non-trivial title generations."""

    def fake_run_summary_generation(*, case, model_spec, longform_template):  # noqa: ANN001
        del case, model_spec, longform_template
        return "news", {
            "title": "Perplexity Adds AI Tax Filing Guidance to Computer",
            "summary": "Perplexity now guides users through federal tax returns.",
        }

    def fake_judge_generated_title(
        *,
        case,
        prompt_type,
        generated_title,
        raw_output,
        judge_model_spec,
    ):  # noqa: ANN001
        del case, prompt_type, generated_title, raw_output, judge_model_spec
        return summary_eval.TitleJudgeVerdict(
            passed=True,
            score=0.92,
            reasoning="Specific, grounded, and clearly better than the reaction-title baseline.",
        )

    monkeypatch.setattr(summary_eval, "_run_summary_generation", fake_run_summary_generation)
    monkeypatch.setattr(summary_eval, "judge_generated_title", fake_judge_generated_title)

    result = summary_eval.run_summary_eval_case(
        suite_name="summary_generation_v1",
        defaults=summary_eval.SummaryEvalDefaults(
            judge_model_spec="anthropic:claude-opus-4-6",
        ),
        case=summary_eval.SummaryEvalCase(
            id="perplexity",
            content_type="news",
            input_text="Perplexity Computer now guides users through federal tax returns.",
            source_title="Wild.",
            existing_title="Wild.",
            bad_titles=["Wild."],
            reference_titles=[
                (
                    "Perplexity Computer Adds Tax Filing Feature That Guides Users "
                    "Through Federal Returns"
                )
            ],
            evaluation_criteria="Good titles should name the product and tax feature.",
        ),
    )

    assert result.passed is True
    assert result.score == 0.92
    assert result.generated_title == "Perplexity Adds AI Tax Filing Guidance to Computer"


def test_run_summary_eval_suite_supports_case_selection(monkeypatch) -> None:
    """Suite runner should filter to a requested case id."""

    def fake_run_summary_eval_case(*, suite_name, defaults, case):  # noqa: ANN001
        del defaults
        return summary_eval.SummaryEvalCaseResult(
            suite=suite_name,
            case_id=case.id,
            content_type=case.content_type,
            model_spec="google:gemini-3.1-flash-lite-preview",
            judge_model_spec="anthropic:claude-opus-4-6",
            prompt_type="news",
            passed=True,
            generated_title="Synthetic title",
        )

    monkeypatch.setattr(summary_eval, "run_summary_eval_case", fake_run_summary_eval_case)

    suite = summary_eval.SummaryEvalSuite(
        suite="summary_generation_v1",
        cases=[
            summary_eval.SummaryEvalCase(
                id="case-1",
                content_type="news",
                input_text="one",
                bad_titles=["bad"],
                reference_titles=["good"],
            ),
            summary_eval.SummaryEvalCase(
                id="case-2",
                content_type="article",
                input_text="two",
                bad_titles=["bad"],
                reference_titles=["good"],
            ),
        ],
    )

    report = summary_eval.run_summary_eval_suite(suite, case_id="case-2")

    assert [result.case_id for result in report.results] == ["case-2"]


def test_run_summary_generation_passes_timeout(monkeypatch) -> None:
    """Live generation calls should be bounded so full evals cannot hang indefinitely."""
    captured: dict[str, Any] = {}

    class FakeResult:
        output = {"title": "Synthetic title"}

    class FakeAgent:
        def run_sync(self, prompt: str, **kwargs: Any) -> FakeResult:
            captured["prompt"] = prompt
            captured["kwargs"] = kwargs
            return FakeResult()

    monkeypatch.setattr(summary_eval, "get_basic_agent", lambda *_args: FakeAgent())

    prompt_type, payload = summary_eval._run_summary_generation(
        case=summary_eval.SummaryEvalCase(
            id="timeout-case",
            content_type="news",
            input_text="Concrete source evidence",
            bad_titles=["bad"],
            reference_titles=["good"],
        ),
        model_spec="openrouter:deepseek/deepseek-v4-flash",
        longform_template="editorial_narrative_v1",
    )

    assert prompt_type == "news"
    assert payload == {"title": "Synthetic title"}
    assert captured["kwargs"] == {
        "model_settings": {"timeout": summary_eval.SUMMARY_EVAL_CALL_TIMEOUT_SECONDS}
    }


def test_judge_generated_title_passes_timeout(monkeypatch) -> None:
    """Judge calls should use the same bounded timeout as generation calls."""
    captured: dict[str, Any] = {}

    class FakeJudgeAgent:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["init_kwargs"] = kwargs

        def run_sync(self, prompt: str, **kwargs: Any) -> Any:
            captured["prompt"] = prompt
            captured["run_kwargs"] = kwargs
            return type(
                "FakeJudgeResult",
                (),
                {
                    "output": summary_eval.TitleJudgeVerdict(
                        passed=True,
                        score=0.9,
                        reasoning="Specific and grounded.",
                    )
                },
            )()

    monkeypatch.setattr(
        summary_eval,
        "build_pydantic_model",
        lambda _model_spec: ("fake-model", {"temperature": 0.0}),
    )
    monkeypatch.setattr(summary_eval, "Agent", FakeJudgeAgent)

    verdict = summary_eval.judge_generated_title(
        case=summary_eval.SummaryEvalCase(
            id="judge-timeout-case",
            content_type="news",
            input_text="Concrete source evidence",
            bad_titles=["bad"],
            reference_titles=["good"],
        ),
        prompt_type="news",
        generated_title="Synthetic title",
        raw_output={"title": "Synthetic title"},
        judge_model_spec="anthropic:claude-opus-4-6",
    )

    assert verdict.passed is True
    assert captured["init_kwargs"]["model_settings"] == {"temperature": 0.0}
    assert captured["run_kwargs"] == {
        "model_settings": {"timeout": summary_eval.SUMMARY_EVAL_CALL_TIMEOUT_SECONDS}
    }


def test_resolve_generation_model_spec_uses_real_app_defaults() -> None:
    """Default eval generation model should follow production summarization routing."""

    article_case = summary_eval.SummaryEvalCase(
        id="article-case",
        content_type="article",
        input_text="Article body",
        bad_titles=["bad"],
        reference_titles=["good"],
    )
    news_case = summary_eval.SummaryEvalCase(
        id="news-case",
        content_type="news",
        input_text="News body",
        bad_titles=["bad"],
        reference_titles=["good"],
    )

    defaults = summary_eval.SummaryEvalDefaults()

    assert (
        summary_eval._resolve_generation_model_spec(case=article_case, defaults=defaults)
        == "openai:gpt-5.6-luna"
    )
    assert (
        summary_eval._resolve_generation_model_spec(case=news_case, defaults=defaults)
        == "openai:gpt-5.6-luna"
    )
