"""Private model-routing settings for onboarding LLM calls."""

from typing import Literal

from pydantic_ai.models.openrouter import (
    OpenRouterModelSettings,
    OpenRouterProviderConfig,
    OpenRouterReasoning,
)
from pydantic_ai.settings import ModelSettings

from app.services.onboarding.config import ONBOARDING_PRIMARY_MODEL, ONBOARDING_PROVIDER_TAG

OpenRouterReasoningEffort = Literal["xhigh", "high", "medium", "low", "minimal", "none"]


def private_openrouter_model_settings(
    *,
    timeout_seconds: int,
    base_settings: ModelSettings | None = None,
    provider_tag: str | None = None,
    reasoning_effort: OpenRouterReasoningEffort | None = None,
) -> OpenRouterModelSettings:
    """Build one typed, fail-closed OpenRouter route for production or evals."""
    settings = OpenRouterModelSettings()
    if base_settings:
        settings.update(base_settings)
    settings["timeout"] = timeout_seconds
    if provider_tag:
        settings["openrouter_provider"] = OpenRouterProviderConfig(
            order=[provider_tag],
            allow_fallbacks=False,
            require_parameters=True,
            data_collection="deny",
            zdr=True,
        )
        settings["openrouter_reasoning"] = (
            OpenRouterReasoning(effort=reasoning_effort, exclude=True)
            if reasoning_effort
            else OpenRouterReasoning(enabled=False, exclude=True)
        )
    return settings


def onboarding_model_settings(model_spec: str, timeout_seconds: int) -> ModelSettings:
    """Return private fail-closed routing for the selected onboarding model."""
    return private_openrouter_model_settings(
        timeout_seconds=timeout_seconds,
        provider_tag=(ONBOARDING_PROVIDER_TAG if model_spec == ONBOARDING_PRIMARY_MODEL else None),
    )


def candidate_models(primary: str, fallbacks: tuple[str, ...]) -> list[str]:
    """Return the fail-closed onboarding route without provider fallback."""
    del fallbacks
    return [primary]
