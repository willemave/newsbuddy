from types import SimpleNamespace

import pytest
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel

from app.services import llm_models


def _settings(**kwargs):
    """Helper to create a stub settings object."""
    return SimpleNamespace(
        openai_api_key=kwargs.get("openai_api_key"),
        anthropic_api_key=kwargs.get("anthropic_api_key"),
        google_api_key=kwargs.get("google_api_key"),
    )


def test_build_pydantic_model_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_models, "get_settings", lambda: _settings(openai_api_key="test-key"))

    model, model_settings = llm_models.build_pydantic_model("gpt-5-mini")

    assert isinstance(model, OpenAIChatModel)
    assert model_settings is None


def test_resolve_model_uses_gpt_5_4_for_openai_default() -> None:
    provider, model_spec = llm_models.resolve_model(llm_models.LLMProvider.OPENAI, None)

    assert provider == llm_models.LLMProvider.OPENAI.value
    assert model_spec == "openai:gpt-5.4"


def test_build_pydantic_model_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm_models,
        "get_settings",
        lambda: _settings(anthropic_api_key="test-key"),
    )

    model, model_settings = llm_models.build_pydantic_model("claude-haiku-4-5-20251001")

    assert isinstance(model, AnthropicModel)
    assert model_settings is None


def test_build_pydantic_model_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_models, "get_settings", lambda: _settings(google_api_key="test-key"))

    model, model_settings = llm_models.build_pydantic_model("gemini-2.5-flash-lite-preview-06-17")

    assert isinstance(model, GoogleModel)
    assert model_settings is not None
    assert model_settings["google_thinking_config"] == {"include_thoughts": False}


def test_build_pydantic_model_google_gemini3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_models, "get_settings", lambda: _settings(google_api_key="test-key"))

    model, model_settings = llm_models.build_pydantic_model("gemini-3-pro-preview")

    assert isinstance(model, GoogleModel)
    assert model_settings is not None
    assert model_settings["google_thinking_config"] == {
        "include_thoughts": False,
        "thinking_level": "low",
    }
