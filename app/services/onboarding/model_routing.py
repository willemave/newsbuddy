"""Private model-routing settings for onboarding LLM calls."""

from typing import Any, cast

from pydantic_ai.settings import ModelSettings

from app.services.onboarding.config import ONBOARDING_PRIMARY_MODEL, ONBOARDING_PROVIDER_TAG


def onboarding_model_settings(model_spec: str, timeout_seconds: int) -> ModelSettings:
    """Return private fail-closed routing for the selected onboarding model."""
    raw_settings: dict[str, Any] = {"timeout": timeout_seconds}
    if model_spec == ONBOARDING_PRIMARY_MODEL:
        raw_settings.update(
            {
                "openrouter_provider": {
                    "order": [ONBOARDING_PROVIDER_TAG],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                    "data_collection": "deny",
                    "zdr": True,
                },
                "openrouter_reasoning": {"enabled": False, "exclude": True},
            }
        )
    return cast(ModelSettings, raw_settings)


def candidate_models(primary: str, fallbacks: tuple[str, ...]) -> list[str]:
    """Return the fail-closed onboarding route without provider fallback."""
    del fallbacks
    return [primary]
