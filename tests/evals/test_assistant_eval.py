"""Tests for the assistant action eval harness."""

from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart

from app.services import assistant_eval
from app.services.admin_conversational_agent import search_knowledge


def test_load_assistant_eval_suite_parses_yaml(tmp_path: Path) -> None:
    """YAML suite loader should parse defaults and cases."""

    dataset = tmp_path / "assistant_actions.yaml"
    dataset.write_text(
        "\n".join(
            [
                "suite: assistant_actions_v1",
                "defaults:",
                "  model_spec: openai:gpt-5.4",
                "  judge_model_spec: openai:gpt-5.4",
                "cases:",
                "  - id: case-1",
                "    query: find Armin Ronacher's blog",
                "    expected_outcome: finds the right blog and subscribes to it",
                "    seed_data:",
                "      daily_digests:",
                "        - local_date: 2026-03-16",
                "          title: Policy and AI moved fast",
                "          summary: Congress, chips, and platforms led the day.",
                "      favorites:",
                "        - url: https://example.com/policy",
                "          title: AI policy landscape",
                "          summary: Policy and regulation updates",
            ]
        ),
        encoding="utf-8",
    )

    suite = assistant_eval.load_assistant_eval_suite(dataset)

    assert suite.suite == "assistant_actions_v1"
    assert suite.defaults.model_spec == "openai:gpt-5.4"
    assert suite.cases[0].id == "case-1"
    assert suite.cases[0].seed_data.daily_digests[0].title == "Policy and AI moved fast"
    assert suite.cases[0].seed_data.favorites[0].title == "AI policy landscape"


def test_seed_case_data_seeds_searchable_favorites() -> None:
    """Eval seed data should create favorited content searchable by term."""

    harness = assistant_eval.create_eval_harness()
    try:
        with harness.session_factory() as db:
            user = assistant_eval.User(
                apple_id="assistant-eval-favorites",
                email="assistant-eval-favorites@example.com",
                full_name="Assistant Eval Favorites",
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            assistant_eval._seed_case_data(
                db,
                user_id=user.id,
                seed_data=assistant_eval.AssistantEvalSeedData(
                    favorites=[
                        assistant_eval.AssistantEvalSeedFavorite(
                            url="https://example.com/ai-policy",
                            title="AI policy landscape",
                            source="Example",
                            summary="Policy and regulation updates",
                        ),
                        assistant_eval.AssistantEvalSeedFavorite(
                            url="https://example.com/sports",
                            title="Sports recap",
                            source="Example",
                            summary="Weekly sports roundup",
                        ),
                    ]
                ),
            )

            hits = search_knowledge(db, user.id, "policy", limit=5)

        assert len(hits) == 1
        assert hits[0].title == "AI policy landscape"
        assert hits[0].url == "https://example.com/ai-policy"
    finally:
        harness.close()


def test_build_assistant_trace_serializes_tool_flow() -> None:
    """Trace builder should preserve ordered tool calls, returns, and assistant text."""

    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="search_web", args={"query": "Armin Ronacher blog"}),
                TextPart(content="I found a likely result."),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="search_web",
                    tool_call_id="call-1",
                    content="Found lucumr.pocoo.org",
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Subscribed to the lucumr feed.")]),
    ]

    trace = assistant_eval.build_assistant_trace(
        query="please find a blog by Armin Ronacher and subscribe to it",
        model_spec="openai:gpt-5.4",
        messages=messages,
    )

    assert trace.final_assistant_text == "Subscribed to the lucumr feed."
    assert [event.kind for event in trace.events] == [
        "tool_call",
        "assistant_text",
        "tool_return",
        "assistant_text",
    ]
    assert trace.events[0].tool_name == "search_web"
    assert "lucumr.pocoo.org" in (trace.events[2].content or "")


def test_run_assistant_eval_case_uses_generic_expected_outcome(monkeypatch) -> None:
    """Case runner should pass expected outcome and trace into the judge."""

    captured: dict[str, str] = {}

    class FakeResult:
        def new_messages(self):
            return [ModelResponse(parts=[TextPart(content="Subscribed to lucumr.")])]

    def fake_run_assistant_turn_sync(*args, **kwargs):
        return FakeResult()

    def fake_judge_assistant_trace(*, expected_outcome, trace, judge_model_spec):
        captured["expected_outcome"] = expected_outcome
        captured["assistant_text"] = trace.final_assistant_text
        captured["judge_model_spec"] = judge_model_spec
        return assistant_eval.AssistantJudgeVerdict(
            passed=True,
            score=1.0,
            reasoning="Looks correct.",
        )

    monkeypatch.setattr(
        assistant_eval,
        "run_assistant_turn_sync",
        fake_run_assistant_turn_sync,
    )
    monkeypatch.setattr(
        assistant_eval,
        "judge_assistant_trace",
        fake_judge_assistant_trace,
    )

    result = assistant_eval.run_assistant_eval_case(
        suite_name="assistant_actions_v1",
        defaults=assistant_eval.AssistantEvalDefaults(
            model_spec="openai:gpt-5.4",
            judge_model_spec="openai:gpt-5.4",
        ),
        case=assistant_eval.AssistantEvalCase(
            id="armin",
            query="please find a blog by Armin Ronacher and subscribe to it",
            expected_outcome="The assistant identifies the correct blog and subscribes to it.",
        ),
    )

    assert result.passed is True
    assert captured["expected_outcome"] == (
        "The assistant identifies the correct blog and subscribes to it."
    )
    assert captured["assistant_text"] == "Subscribed to lucumr."
    assert captured["judge_model_spec"] == "openai:gpt-5.4"


def test_run_assistant_eval_case_requires_feed_options_when_expected(monkeypatch) -> None:
    """Cases can fail even with good text when feed-option metadata is missing."""

    class FakeResult:
        def new_messages(self):
            return [
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="find_feed_options",
                            args={"query": "Armin Ronacher blog"},
                            tool_call_id="call-1",
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        TextPart(
                            content="Tool output failed validation before it could be rendered."
                        )
                    ]
                ),
                ModelResponse(parts=[TextPart(content="Here is Armin Ronacher's blog.")]),
            ]

    def fake_run_assistant_turn_sync(*args, **kwargs):
        return FakeResult()

    def fake_judge_assistant_trace(*, expected_outcome, trace, judge_model_spec):
        return assistant_eval.AssistantJudgeVerdict(
            passed=True,
            score=1.0,
            reasoning="The assistant text looks correct.",
        )

    monkeypatch.setattr(
        assistant_eval,
        "run_assistant_turn_sync",
        fake_run_assistant_turn_sync,
    )
    monkeypatch.setattr(
        assistant_eval,
        "judge_assistant_trace",
        fake_judge_assistant_trace,
    )

    result = assistant_eval.run_assistant_eval_case(
        suite_name="assistant_actions_v1",
        defaults=assistant_eval.AssistantEvalDefaults(
            model_spec="openai:gpt-5.4",
            judge_model_spec="openai:gpt-5.4",
        ),
        case=assistant_eval.AssistantEvalCase(
            id="armin",
            query="please find a blog by Armin Ronacher and subscribe to it",
            expected_outcome="The assistant identifies the correct blog.",
            expected_feed_options=True,
        ),
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.reasoning is not None
    assert "Missing validated feed options" in result.reasoning
    assert result.debug_state.feed_options == []
