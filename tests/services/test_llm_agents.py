from __future__ import annotations

from typing import Any

from pydantic_ai import NativeOutput

from app.models.metadata.summaries import GeneratedNewsSummary
from app.services import llm_agents


def test_openrouter_structured_outputs_use_native_json_schema(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeAgent:
        @classmethod
        def __class_getitem__(cls, _item):  # noqa: ANN001
            return cls

        def __init__(  # noqa: ANN001
            self,
            model,
            *,
            output_type,
            system_prompt,
            model_settings,
            output_retries=None,
        ):
            captured["model"] = model
            captured["output_type"] = output_type
            captured["system_prompt"] = system_prompt
            captured["model_settings"] = model_settings
            captured["output_retries"] = output_retries

    monkeypatch.setattr(
        llm_agents,
        "build_pydantic_model",
        lambda _model_spec: ("model", {"openrouter_provider": {"require_parameters": True}}),
    )
    monkeypatch.setattr(llm_agents, "Agent", FakeAgent)

    llm_agents.get_basic_agent(
        "openrouter:deepseek/deepseek-v4-flash",
        GeneratedNewsSummary,
        "system prompt",
    )

    output_type = captured["output_type"]
    assert isinstance(output_type, NativeOutput)
    assert output_type.outputs is GeneratedNewsSummary
    assert output_type.strict is True
    assert captured["output_retries"] == llm_agents.OPENROUTER_STRUCTURED_OUTPUT_RETRIES


def test_openrouter_text_output_stays_text(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeAgent:
        @classmethod
        def __class_getitem__(cls, _item):  # noqa: ANN001
            return cls

        def __init__(self, model, *, output_type, system_prompt, model_settings):  # noqa: ANN001
            captured["output_type"] = output_type

    monkeypatch.setattr(llm_agents, "build_pydantic_model", lambda _model_spec: ("model", None))
    monkeypatch.setattr(llm_agents, "Agent", FakeAgent)

    llm_agents.get_basic_agent("openrouter:deepseek/deepseek-v4-flash", str, "system prompt")

    assert captured["output_type"] is str
