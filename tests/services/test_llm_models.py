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
        google_cloud_project=kwargs.get("google_cloud_project"),
        google_cloud_location=kwargs.get("google_cloud_location", "global"),
        cerebras_api_key=kwargs.get("cerebras_api_key"),
    )


def test_build_pydantic_model_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_models, "get_settings", lambda: _settings(openai_api_key="test-key"))

    model, model_settings = llm_models.build_pydantic_model("gpt-5-mini")

    assert isinstance(model, OpenAIChatModel)
    assert model_settings is None


def test_build_pydantic_model_openai_accepts_user_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_models, "get_settings", lambda: _settings(openai_api_key=None))

    model, model_settings = llm_models.build_pydantic_model(
        "gpt-5-mini",
        api_key_override="user-openai-key",
    )

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
    assert model._provider.name == "google-vertex"
    assert model_settings is not None
    assert model_settings["google_thinking_config"] == {"include_thoughts": False}


def test_build_pydantic_model_google_gemini3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_models, "get_settings", lambda: _settings(google_api_key="test-key"))

    model, model_settings = llm_models.build_pydantic_model("gemini-3-pro-preview")

    assert isinstance(model, GoogleModel)
    assert model._provider.name == "google-vertex"
    assert model_settings is not None
    assert model_settings["google_thinking_config"] == {
        "include_thoughts": False,
        "thinking_level": "low",
    }


def test_build_pydantic_model_google_uses_project_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llm_models,
        "get_settings",
        lambda: _settings(
            google_api_key="test-key",
            google_cloud_project="news-app-prod",
            google_cloud_location="us-central1",
        ),
    )

    model, _ = llm_models.build_pydantic_model("gemini-3-flash-preview")

    assert isinstance(model, GoogleModel)
    assert model._provider.name == "google-vertex"


def test_resolve_effective_api_key_prefers_user_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm_models,
        "get_settings",
        lambda: _settings(openai_api_key="platform-key"),
    )
    monkeypatch.setattr(
        llm_models,
        "get_user_llm_api_key",
        lambda db, user_id, provider: "user-key",
    )

    resolved = llm_models.resolve_effective_api_key(
        db=object(),
        user_id=123,
        model_spec="openai:gpt-5-mini",
    )

    assert resolved == "user-key"


def test_resolve_effective_api_key_falls_back_to_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm_models,
        "get_settings",
        lambda: _settings(anthropic_api_key="platform-key"),
    )
    monkeypatch.setattr(
        llm_models,
        "get_user_llm_api_key",
        lambda db, user_id, provider: None,
    )

    resolved = llm_models.resolve_effective_api_key(
        db=object(),
        user_id=123,
        model_spec="anthropic:claude-haiku-4-5-20251001",
    )

    assert resolved == "platform-key"
