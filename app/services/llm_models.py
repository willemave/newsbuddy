"""Shared pydantic-ai model construction helpers."""

from __future__ import annotations

from enum import Enum, StrEnum

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.cerebras import CerebrasProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.repositories.user_integration_repository import get_user_llm_api_key


class LLMProvider(StrEnum):
    """Supported LLM providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    CEREBRAS = "cerebras"
    DEEP_RESEARCH = "deep_research"


# Provider prefixes and defaults are kept in sync with chat_agent usage.
PROVIDER_PREFIXES: dict[str, str] = {
    LLMProvider.OPENAI.value: "openai",
    LLMProvider.ANTHROPIC.value: "anthropic",
    LLMProvider.GOOGLE.value: "google-gla",
    LLMProvider.CEREBRAS.value: "cerebras",
    LLMProvider.DEEP_RESEARCH.value: "deep_research",
}

PROVIDER_DEFAULTS: dict[str, str] = {
    LLMProvider.OPENAI.value: "openai:gpt-5.4",
    LLMProvider.ANTHROPIC.value: "anthropic:claude-opus-4-5-20251101",
    LLMProvider.GOOGLE.value: "google-gla:gemini-3-pro-preview",
    LLMProvider.CEREBRAS.value: "cerebras:zai-glm-4.7",
    LLMProvider.DEEP_RESEARCH.value: "deep_research:o4-mini-deep-research-2025-06-26",
}

# Deep research model constant for easy reference
DEEP_RESEARCH_MODEL = "o4-mini-deep-research-2025-06-26"

DEFAULT_PROVIDER = LLMProvider.OPENAI.value
DEFAULT_MODEL = PROVIDER_DEFAULTS[DEFAULT_PROVIDER]
PREFIX_TO_PROVIDER: dict[str, str] = {
    prefix: provider for provider, prefix in PROVIDER_PREFIXES.items()
}


def resolve_model(
    provider: LLMProvider | str | None,
    model_hint: str | None,
) -> tuple[str, str]:
    """Resolve provider + model hint into canonical provider and full model spec.

    Args:
        provider: Optional provider enum/string (openai|anthropic|google). Defaults to openai.
        model_hint: Optional specific model name or already-prefixed model spec.

    Returns:
        Tuple of (canonical_provider_name, model_spec).
    """

    def _normalize_provider_name(provider_value: LLMProvider | str | None) -> str:
        if provider_value is None:
            return DEFAULT_PROVIDER
        raw = provider_value.value if isinstance(provider_value, Enum) else str(provider_value)
        return PREFIX_TO_PROVIDER.get(raw, raw)

    provider_name = _normalize_provider_name(provider)

    if model_hint and ":" in model_hint:
        provider_prefix = model_hint.split(":", 1)[0]
        hinted_provider = PREFIX_TO_PROVIDER.get(provider_prefix, provider_prefix)
        canonical_provider = (
            hinted_provider if hinted_provider in PROVIDER_DEFAULTS else provider_name
        )
        return canonical_provider, model_hint

    model_prefix = PROVIDER_PREFIXES.get(provider_name, provider_name)
    if model_hint:
        return provider_name, f"{model_prefix}:{model_hint}"

    return provider_name, PROVIDER_DEFAULTS.get(provider_name, DEFAULT_MODEL)


def resolve_model_provider(model_spec: str) -> str:
    """Resolve the canonical provider name for a model spec."""
    if ":" in model_spec:
        prefix = model_spec.split(":", 1)[0]
        return PREFIX_TO_PROVIDER.get(prefix, prefix)
    if model_spec.startswith("gpt-"):
        return LLMProvider.OPENAI.value
    if model_spec.startswith("claude-"):
        return LLMProvider.ANTHROPIC.value
    if model_spec.startswith("gemini"):
        return LLMProvider.GOOGLE.value
    return DEFAULT_PROVIDER


def resolve_effective_api_key(
    *,
    db: Session | None,
    user_id: int | None,
    provider: str | None = None,
    model_spec: str | None = None,
) -> str | None:
    """Prefer a user-managed provider key, then fall back to platform credentials."""
    provider_name = provider or (resolve_model_provider(model_spec) if model_spec else None)
    if provider_name is None:
        return None

    if db is not None and user_id is not None:
        user_api_key = get_user_llm_api_key(db, user_id=user_id, provider=provider_name)
        if user_api_key:
            return user_api_key

    settings = get_settings()
    platform_keys = {
        LLMProvider.OPENAI.value: settings.openai_api_key,
        LLMProvider.ANTHROPIC.value: settings.anthropic_api_key,
        LLMProvider.GOOGLE.value: settings.google_api_key,
        LLMProvider.CEREBRAS.value: settings.cerebras_api_key,
    }
    return platform_keys.get(provider_name)


def _build_openai_responses_model_settings() -> OpenAIResponsesModelSettings:
    """Return default settings for OpenAI Responses models.

    We disable reasoning item ID replay because chat history is rewritten for
    user display before persistence, which makes provider-side message IDs
    unsafe to resend. We keep 24h prompt-cache retention enabled so long,
    repeated system-prompt prefixes can stay warm longer.
    """

    return {
        "openai_prompt_cache_retention": "24h",
        "openai_send_reasoning_ids": False,
    }


def build_pydantic_model(
    model_spec: str,
    *,
    api_key_override: str | None = None,
) -> tuple[Model | str, ModelSettings | None]:
    """Construct a pydantic-ai Model with explicit providers where required.

    Args:
        model_spec: Full model spec string (e.g., ``google-gla:gemini-3-pro-preview``).

    Returns:
        Tuple of (model, model_settings). ``model`` is either a configured ``Model`` instance
        or the raw ``model_spec`` when no specific provider wiring is required. ``model_settings``
        is populated when a provider needs extra request settings.
    """
    settings = get_settings()

    provider_prefix = None
    model_name = model_spec
    if ":" in model_spec:
        provider_prefix, model_name = model_spec.split(":", 1)

    if (
        provider_prefix in {"google-gla", "google"}
        or model_spec.startswith("google-gla:")
        or model_spec.startswith("gemini")
    ):
        resolved_api_key = api_key_override or settings.google_api_key
        if not resolved_api_key and not settings.google_cloud_project:
            raise ValueError("GOOGLE_API_KEY not configured in settings.")
        model_to_use = (
            model_name
            if provider_prefix
            else (model_spec.split(":", 1)[1] if ":" in model_spec else model_spec)
        )
        provider_kwargs: dict[str, str | bool] = {"vertexai": True}
        if api_key_override:
            provider_kwargs = {"api_key": api_key_override}
        elif settings.google_cloud_project:
            provider_kwargs["project"] = settings.google_cloud_project
            provider_kwargs["location"] = settings.google_cloud_location
        else:
            provider_kwargs["api_key"] = resolved_api_key

        model = GoogleModel(model_to_use, provider=GoogleProvider(**provider_kwargs))
        # Configure thinking for Google models – suppress thought traces and
        # explicitly lower thinking depth on Gemini 3 to reduce latency.
        thinking_config: dict[str, object] = {"include_thoughts": False}
        if model_to_use.startswith("gemini-3"):
            thinking_config["thinking_level"] = "low"
        model_settings = GoogleModelSettings(google_thinking_config=thinking_config)
        return model, model_settings

    if provider_prefix == "anthropic" or model_spec.startswith("claude-"):
        resolved_api_key = api_key_override or settings.anthropic_api_key
        if not resolved_api_key:
            raise ValueError("ANTHROPIC_API_KEY not configured in settings.")
        provider = AnthropicProvider(api_key=resolved_api_key)
        model_to_use = model_name if provider_prefix == "anthropic" else model_spec
        return AnthropicModel(model_to_use, provider=provider), None

    if provider_prefix == "cerebras" or model_spec.startswith("cerebras:"):
        resolved_api_key = api_key_override or settings.cerebras_api_key
        if not resolved_api_key:
            raise ValueError("CEREBRAS_API_KEY not configured in settings.")
        model_to_use = (
            model_name
            if provider_prefix
            else (model_spec.split(":", 1)[1] if ":" in model_spec else model_spec)
        )
        provider = CerebrasProvider(api_key=resolved_api_key)
        return OpenAIChatModel(model_to_use, provider=provider), None

    if (
        provider_prefix == "openai"
        or model_spec.startswith("openai:")
        or model_spec.startswith("gpt-")
    ):
        resolved_api_key = api_key_override or settings.openai_api_key
        if not resolved_api_key:
            raise ValueError("OPENAI_API_KEY not configured in settings.")
        model_to_use = (
            model_name
            if provider_prefix
            else (model_spec.split(":", 1)[1] if ":" in model_spec else model_spec)
        )
        return (
            OpenAIResponsesModel(model_to_use, provider=OpenAIProvider(api_key=resolved_api_key)),
            _build_openai_responses_model_settings(),
        )

    return model_spec, None


def is_deep_research_provider(provider: LLMProvider | str | None) -> bool:
    """Check if the given provider is deep research.

    Args:
        provider: Provider enum or string.

    Returns:
        True if deep research provider.
    """
    if provider is None:
        return False
    raw = provider.value if isinstance(provider, Enum) else str(provider)
    return raw == LLMProvider.DEEP_RESEARCH.value


def is_deep_research_model(model_spec: str | None) -> bool:
    """Check if the given model spec is for deep research.

    Args:
        model_spec: Model specification string.

    Returns:
        True if this is a deep research model.
    """
    if not model_spec:
        return False
    return (
        model_spec.startswith("deep_research:")
        or "deep-research" in model_spec
        or model_spec == DEEP_RESEARCH_MODEL
    )
