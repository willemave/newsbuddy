"""Shared pydantic-ai model construction helpers."""

from __future__ import annotations

from enum import Enum, StrEnum

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.cerebras import CerebrasProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider

from app.core.settings import get_settings


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

DEFAULT_PROVIDER = LLMProvider.ANTHROPIC.value
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
        provider: Optional provider enum/string (openai|anthropic|google). Defaults to anthropic.
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


def build_pydantic_model(model_spec: str) -> tuple[Model | str, GoogleModelSettings | None]:
    """Construct a pydantic-ai Model with explicit providers where required.

    Args:
        model_spec: Full model spec string (e.g., ``google-gla:gemini-3-pro-preview``).

    Returns:
        Tuple of (model, model_settings). ``model`` is either a configured ``Model`` instance
        or the raw ``model_spec`` when no specific provider wiring is required. ``model_settings``
        is only populated for Google models to suppress thinking traces.
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
        if not settings.google_api_key:
            raise ValueError("GOOGLE_API_KEY not configured in settings.")
        model_to_use = (
            model_name
            if provider_prefix
            else (model_spec.split(":", 1)[1] if ":" in model_spec else model_spec)
        )
        model = GoogleModel(model_to_use, provider=GoogleProvider(api_key=settings.google_api_key))
        # Configure thinking for Google models – suppress thought traces and
        # explicitly lower thinking depth on Gemini 3 to reduce latency.
        thinking_config: dict[str, object] = {"include_thoughts": False}
        if model_to_use.startswith("gemini-3"):
            thinking_config["thinking_level"] = "low"
        model_settings = GoogleModelSettings(google_thinking_config=thinking_config)
        return model, model_settings

    if provider_prefix == "anthropic" or model_spec.startswith("claude-"):
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY not configured in settings.")
        provider = AnthropicProvider(api_key=settings.anthropic_api_key)
        model_to_use = model_name if provider_prefix == "anthropic" else model_spec
        return AnthropicModel(model_to_use, provider=provider), None

    if provider_prefix == "cerebras" or model_spec.startswith("cerebras:"):
        if not settings.cerebras_api_key:
            raise ValueError("CEREBRAS_API_KEY not configured in settings.")
        model_to_use = (
            model_name
            if provider_prefix
            else (model_spec.split(":", 1)[1] if ":" in model_spec else model_spec)
        )
        provider = CerebrasProvider(api_key=settings.cerebras_api_key)
        return OpenAIChatModel(model_to_use, provider=provider), None

    if (
        provider_prefix == "openai"
        or model_spec.startswith("openai:")
        or model_spec.startswith("gpt-")
    ):
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY not configured in settings.")
        model_to_use = (
            model_name
            if provider_prefix
            else (model_spec.split(":", 1)[1] if ":" in model_spec else model_spec)
        )
        return (
            OpenAIChatModel(model_to_use, provider=OpenAIProvider(api_key=settings.openai_api_key)),
            None,
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
