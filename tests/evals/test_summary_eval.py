"""Tests for the fixture-backed summary generation eval harness."""

from __future__ import annotations

from pathlib import Path

from app.services import summary_eval


def test_load_summary_eval_suite_parses_yaml(tmp_path: Path) -> None:
    """Summary eval suite loader should parse defaults and cases."""
    dataset = tmp_path / "summary_generation.yaml"
    dataset.write_text(
        "\n".join(
            [
                "suite: summary_generation_v1",
                "defaults:",
                "  model_spec: openai:gpt-5.4",
                "  judge_model_spec: openai:gpt-5.4",
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
                "    notes: Replace reaction text with the actual takeaway.",
                "    input_text: >",
                "      The source announces a concrete product launch and why it matters.",
            ]
        ),
        encoding="utf-8",
    )

    suite = summary_eval.load_summary_eval_suite(dataset)

    assert suite.suite == "summary_generation_v1"
    assert suite.defaults.model_spec == "openai:gpt-5.4"
    assert suite.cases[0].id == "case-1"
    assert suite.cases[0].bad_titles == ["wow"]


def test_run_summary_eval_case_fails_when_generated_title_matches_bad_title(
    monkeypatch,
) -> None:
    """Runner should hard-fail exact matches against known bad titles."""

    def fake_run_summary_generation(*, case, model_spec, longform_template):  # noqa: ANN001
        del model_spec, longform_template
        return "news_digest", {"title": case.bad_titles[0], "summary": "ignored"}

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
            notes="Bad titles are generic reactions.",
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
        return "news_digest", {
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
            model_spec="openai:gpt-5.4",
            judge_model_spec="openai:gpt-5.4",
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
            notes="Good titles should name the product and tax feature.",
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
            model_spec="openai:gpt-5.4",
            judge_model_spec="openai:gpt-5.4",
            prompt_type="news_digest",
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
