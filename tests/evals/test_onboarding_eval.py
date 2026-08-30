from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from app.services.onboarding.internal_models import _AudioLane, _AudioPlanOutput
from app.services.onboarding_eval import (
    ONBOARDING_EVAL_CANDIDATES,
    OnboardingAudioPlanEvalCase,
    OnboardingEvalCandidate,
    _normalize_strict_json_schema,
    build_audio_plan_judge_prompt,
    build_candidate_model_settings,
    compute_audio_plan_checks,
    estimate_eval_call_count,
    load_onboarding_audio_plan_eval_suite,
    resolve_onboarding_eval_candidates,
)


def test_load_onboarding_audio_plan_eval_suite() -> None:
    suite = load_onboarding_audio_plan_eval_suite(Path("tests/evals/onboarding_audio_plan.yaml"))

    assert suite.suite == "onboarding_audio_plan_tool_calls_v1"
    assert [case.id for case in suite.cases] == [
        "technical_and_business",
        "science_and_practical_business",
        "policy_and_economics",
    ]


def test_deepseek_candidate_settings_pin_provider_and_fail_closed() -> None:
    candidate = ONBOARDING_EVAL_CANDIDATES["deepseek_coreweave"]

    settings = cast(
        dict[str, Any],
        build_candidate_model_settings(
            candidate,
            {"temperature": 0.4},
            timeout_seconds=9,
        ),
    )

    assert settings["temperature"] == 0.4
    assert settings["timeout"] == 9
    assert settings["openrouter_provider"] == {
        "order": ["coreweave/fp8"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
        "zdr": True,
    }
    assert settings["openrouter_reasoning"] == {"enabled": False, "exclude": True}


def test_candidate_settings_can_request_low_reasoning() -> None:
    candidate = OnboardingEvalCandidate(
        "glm",
        "openrouter:z-ai/glm-5.3",
        "z-ai/fp8",
        "low",
    )

    settings = cast(
        dict[str, Any], build_candidate_model_settings(candidate, None, timeout_seconds=15)
    )

    assert settings["openrouter_reasoning"] == {"effort": "low", "exclude": True}


def test_compute_audio_plan_checks_scores_prompt_contract() -> None:
    plan = _AudioPlanOutput(
        topic_summary="AI engineering, startup strategy, and product leadership",
        inferred_topics=["AI engineering", "startup strategy", "product leadership"],
        lanes=[
            _AudioLane(
                name="AI engineering",
                goal="Find practical engineering analysis",
                target="feeds",
                queries=[
                    "advanced AI engineering newsletter RSS feeds",
                    "machine learning systems architecture blogs",
                ],
            ),
            _AudioLane(
                name="Startup strategy",
                goal="Find grounded startup analysis",
                target="podcasts",
                queries=[
                    "startup strategy operator podcast RSS",
                    "early stage company building interviews",
                ],
            ),
            _AudioLane(
                name="Product communities",
                goal="Find product leadership discussion",
                target="reddit",
                queries=[
                    "best product leadership subreddits discussions",
                    "product management strategy reddit communities",
                ],
            ),
        ],
    )

    checks, score = compute_audio_plan_checks(plan)

    assert all(checks.values())
    assert score == 1.0


def test_resolve_candidates_rejects_unknown_alias() -> None:
    with pytest.raises(ValueError, match="Unknown onboarding eval candidates: missing"):
        resolve_onboarding_eval_candidates(["missing"])


def test_estimate_eval_call_count_includes_candidates_and_judgments() -> None:
    assert estimate_eval_call_count(case_count=3, candidate_count=6, runs=1, judge=True) == 36
    assert estimate_eval_call_count(case_count=3, candidate_count=6, runs=2, judge=False) == 36


def test_judge_prompt_focuses_only_on_perceived_link_quality() -> None:
    prompt = build_audio_plan_judge_prompt(
        case=OnboardingAudioPlanEvalCase(
            id="case",
            transcript="AI engineering and product leadership",
        ),
        candidate_output={"lanes": []},
    )

    assert "relevance" in prompt
    assert "diversity" in prompt
    assert "practical search phrases" in prompt
    assert "reference" not in prompt


def test_normalize_strict_json_schema_closes_nested_objects() -> None:
    schema = {
        "type": "object",
        "properties": {
            "nested": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            }
        },
    }

    _normalize_strict_json_schema(schema)

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["nested"]
    nested = schema["properties"]["nested"]
    assert nested["additionalProperties"] is False
    assert nested["required"] == ["value"]
