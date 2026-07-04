from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel
from sqlalchemy.orm import Session

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
        openrouter_api_key=kwargs.get("openrouter_api_key"),
        openrouter_ignored_providers=kwargs.get("openrouter_ignored_providers", []),
    )


def test_build_pydantic_model_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_models, "get_settings", lambda: _settings(openai_api_key="test-key"))

    model, model_settings = llm_models.build_pydantic_model("gpt-5.4-mini")

    assert isinstance(model, OpenAIResponsesModel)
    assert model_settings == {
        "openai_prompt_cache_retention": "24h",
        "openai_send_reasoning_ids": False,
    }


def test_build_pydantic_model_openai_accepts_user_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_models, "get_settings", lambda: _settings(openai_api_key=None))

    model, model_settings = llm_models.build_pydantic_model(
        "gpt-5.4-mini",
        api_key_override="user-openai-key",
    )

    assert isinstance(model, OpenAIResponsesModel)
    assert model_settings == {
        "openai_prompt_cache_retention": "24h",
        "openai_send_reasoning_ids": False,
    }


def test_build_pydantic_model_openai_accepts_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_models, "get_settings", lambda: _settings(openai_api_key="test-key"))

    model, model_settings = llm_models.build_pydantic_model(
        "gpt-5.5",
        openai_reasoning_effort="low",
    )

    assert isinstance(model, OpenAIResponsesModel)
    assert model_settings == {
        "openai_prompt_cache_retention": "24h",
        "openai_reasoning_effort": "low",
        "openai_send_reasoning_ids": False,
    }


def test_resolve_model_uses_gpt_5_5_for_openai_default() -> None:
    provider, model_spec = llm_models.resolve_model(llm_models.LLMProvider.OPENAI, None)

    assert provider == llm_models.LLMProvider.OPENAI.value
    assert model_spec == "openai:gpt-5.5"


def test_resolve_model_google_default_stays_on_gemini() -> None:
    provider, model_spec = llm_models.resolve_model(llm_models.LLMProvider.GOOGLE, None)

    assert provider == llm_models.LLMProvider.GOOGLE.value
    assert model_spec == "google:gemini-3.1-flash-lite-preview"


def test_build_pydantic_model_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm_models,
        "get_settings",
        lambda: _settings(anthropic_api_key="test-key"),
    )

    model, model_settings = llm_models.build_pydantic_model("claude-opus-4-6")

    assert isinstance(model, AnthropicModel)
    assert model_settings is None


def test_build_pydantic_model_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_models, "get_settings", lambda: _settings(google_api_key="test-key"))

    model, model_settings = llm_models.build_pydantic_model("gemini-3.1-flash-lite-preview")

    assert isinstance(model, GoogleModel)
    assert model._provider.name == "google-vertex"
    assert model_settings is not None
    assert cast(dict[str, object], model_settings)["google_thinking_config"] == {
        "include_thoughts": False,
        "thinking_level": "low",
    }


def test_build_pydantic_model_google_gemini3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_models, "get_settings", lambda: _settings(google_api_key="test-key"))

    model, model_settings = llm_models.build_pydantic_model("gemini-3.1-flash-lite-preview")

    assert isinstance(model, GoogleModel)
    assert model._provider.name == "google-vertex"
    assert model_settings is not None
    assert cast(dict[str, object], model_settings)["google_thinking_config"] == {
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


def test_build_pydantic_model_openrouter_uses_native_json_schema_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llm_models,
        "get_settings",
        lambda: _settings(openrouter_api_key="test-key"),
    )

    model, model_settings = llm_models.build_pydantic_model("openrouter:deepseek/deepseek-v4-flash")

    assert isinstance(model, OpenRouterModel)
    assert model.profile.supports_json_schema_output is True
    assert model.profile.supports_json_object_output is True
    assert model_settings == {
        "openrouter_provider": {
            "require_parameters": True,
            "sort": llm_models.OPENROUTER_PROVIDER_SORT,
        },
        "openrouter_reasoning": llm_models.OPENROUTER_REASONING_CONFIG,
        "timeout": llm_models.OPENROUTER_MODEL_TIMEOUT_SECONDS,
    }


def test_openrouter_provider_config_includes_deny_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llm_models,
        "get_settings",
        lambda: _settings(openrouter_ignored_providers=["Alibaba"]),
    )

    config = llm_models.openrouter_provider_config()

    assert config == {
        "require_parameters": True,
        "sort": llm_models.OPENROUTER_PROVIDER_SORT,
        "ignore": ["Alibaba"],
    }


def test_openrouter_provider_config_omits_empty_deny_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_models, "get_settings", lambda: _settings())

    config = llm_models.openrouter_provider_config()

    assert "ignore" not in config


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
        db=cast(Session, object()),
        user_id=123,
        model_spec="openai:gpt-5.4-mini",
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
        db=cast(Session, object()),
        user_id=123,
        model_spec="anthropic:claude-opus-4-6",
    )

    assert resolved == "platform-key"
