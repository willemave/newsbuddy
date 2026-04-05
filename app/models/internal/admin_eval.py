"""Shared schemas for admin eval workflows."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

EvalContentType = Literal["article", "podcast", "news"]
LongformTemplate = Literal[
    "long_bullets_v1",
    "interleaved_v2",
    "structured_v1",
    "editorial_narrative_v1",
]

EVAL_MODEL_SPECS: dict[str, str] = {
    "flash_lite": "google:gemini-3.1-flash-lite-preview",
    "opus": "anthropic:claude-opus-4-5-20251101",
    "gemini_3_pro": "google-gla:gemini-3-pro-preview",
    "flash_2": "google-gla:gemini-3-flash-preview",
    "gpt_5_4": "openai:gpt-5.4",
    "cerebras_glm_4_7": "cerebras:zai-glm-4.7",
}
EVAL_MODEL_LABELS: dict[str, str] = {
    "flash_lite": "Gemini 3.1 Flash Lite",
    "opus": "Opus",
    "gemini_3_pro": "Gemini 3 Pro",
    "flash_2": "Flash 2",
    "gpt_5_4": "GPT 5.4",
    "cerebras_glm_4_7": "Cerebras GLM-4.7",
}
LONGFORM_TEMPLATE_LABELS: dict[str, str] = {
    "long_bullets_v1": "Long Bullets v1",
    "interleaved_v2": "Interleaved v2",
    "structured_v1": "Structured v1",
    "editorial_narrative_v1": "Editorial Narrative v1",
}
KNOWN_ADMIN_EVAL_MODELS = tuple(EVAL_MODEL_SPECS)
DEFAULT_ADMIN_EVAL_CONTENT_TYPES: tuple[EvalContentType, ...] = ("article", "podcast", "news")


class ModelPricing(BaseModel):
    """Optional pricing inputs for estimated cost calculations."""

    input_per_million_usd: float | None = Field(default=None, ge=0)
    output_per_million_usd: float | None = Field(default=None, ge=0)


class AdminEvalRunRequest(BaseModel):
    """Request payload for running an admin eval batch."""

    content_types: list[EvalContentType] = Field(
        default_factory=lambda: list(DEFAULT_ADMIN_EVAL_CONTENT_TYPES)
    )
    models: list[str] = Field(default_factory=lambda: list(KNOWN_ADMIN_EVAL_MODELS))
    longform_template: LongformTemplate = "editorial_narrative_v1"
    recent_pool_size: int = Field(default=200, ge=10, le=2000)
    sample_size: int = Field(default=3, ge=1, le=100)
    seed: int | None = Field(default=None)
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)

    @field_validator("content_types")
    @classmethod
    def validate_content_types(cls, value: list[EvalContentType]) -> list[EvalContentType]:
        """Ensure at least one content type is selected."""

        deduped = list(dict.fromkeys(value))
        if not deduped:
            raise ValueError("At least one content type must be selected")
        return deduped

    @field_validator("models")
    @classmethod
    def validate_models(cls, value: list[str]) -> list[str]:
        """Ensure all models are known aliases and list is not empty."""

        deduped = list(dict.fromkeys(value))
        if not deduped:
            raise ValueError("At least one model must be selected")

        unknown = [model for model in deduped if model not in KNOWN_ADMIN_EVAL_MODELS]
        if unknown:
            raise ValueError(f"Unknown model aliases: {', '.join(unknown)}")
        return deduped

    @model_validator(mode="after")
    def validate_sample_bounds(self) -> "AdminEvalRunRequest":
        """Ensure sample size does not exceed pool size."""

        if self.sample_size > self.recent_pool_size:
            raise ValueError("sample_size must be <= recent_pool_size")
        return self
