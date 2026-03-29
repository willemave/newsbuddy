"""Shared summarization flow using pydantic-ai agents."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from app.core.logging import get_logger
from app.models.metadata import (
    BulletedSummary,
    ContentType,
    EditorialNarrativeSummary,
    InterleavedSummaryV2,
    NewsSummary,
    StructuredSummary,
    SummaryPayload,
)
from app.services.llm_agents import get_basic_agent
from app.services.llm_models import resolve_model
from app.services.llm_prompts import generate_summary_prompt

logger = get_logger(__name__)

MAX_SUMMARIZATION_PAYLOAD_CHARS = 220_000
DEFAULT_ARTICLE_MODEL_SPEC = "openai:gpt-5.4-mini"

SummarizationPromptType = Literal[
    "structured",
    "interleaved",
    "long_bullets",
    "news_digest",
    "editorial_narrative",
]
SummarizationOutputType = (
    type[StructuredSummary]
    | type[InterleavedSummaryV2]
    | type[BulletedSummary]
    | type[EditorialNarrativeSummary]
    | type[NewsSummary]
)

CONTEXT_LENGTH_ERROR_HINTS: tuple[str, ...] = (
    "context_length_exceeded",
    "input tokens exceed",
    "maximum context length",
    "too many tokens",
    "prompt is too long",
)

PROVIDER_PRECONDITION_ERROR_HINTS: tuple[str, ...] = (
    "failed_precondition",
    "user location is not supported",
    "not supported for the api use",
)

PROVIDER_CONFIG_ERROR_HINTS: tuple[str, ...] = (
    "not configured in settings",
    "api key is required",
    "api key not configured",
)


def _finalize_summary(summary: SummaryPayload) -> SummaryPayload:
    """Apply lightweight cleanup to keep summaries consistent."""
    if isinstance(summary, StructuredSummary) and summary.quotes:
        summary.quotes = [
            quote for quote in summary.quotes if len((quote.text or "").strip()) >= 10
        ]
    if isinstance(summary, InterleavedSummaryV2) and summary.quotes:
        summary.quotes = [
            quote for quote in summary.quotes if len((quote.text or "").strip()) >= 10
        ]
    if isinstance(summary, EditorialNarrativeSummary) and summary.quotes:
        summary.quotes = [
            quote for quote in summary.quotes if len((quote.text or "").strip()) >= 10
        ]
    return summary


def _normalize_content_type(content_type: str | ContentType) -> str:
    return content_type.value if isinstance(content_type, ContentType) else str(content_type)


def resolve_summarization_output_type(
    prompt_type: SummarizationPromptType,
) -> SummarizationOutputType:
    """Return the pydantic output type for a canonical summarization prompt type."""
    if prompt_type == "news_digest":
        return NewsSummary
    if prompt_type == "editorial_narrative":
        return EditorialNarrativeSummary
    if prompt_type == "long_bullets":
        return BulletedSummary
    if prompt_type == "interleaved":
        return InterleavedSummaryV2
    return StructuredSummary


def resolve_summarization_spec(
    content_type: str | ContentType,
    default_models: Mapping[str, str] | None = None,
) -> tuple[SummarizationPromptType, SummarizationOutputType, str]:
    """Resolve content type into prompt routing, output schema, and default model."""
    models = default_models or DEFAULT_SUMMARIZATION_MODELS
    normalized_type = _normalize_content_type(content_type)
    default_article_model = models.get("article", DEFAULT_ARTICLE_MODEL_SPEC)

    if normalized_type in {"article", "podcast"}:
        prompt_type: SummarizationPromptType = "editorial_narrative"
        default_model_spec = models.get(normalized_type, default_article_model)
    elif normalized_type == "news":
        prompt_type = "news_digest"
        default_model_spec = models.get("news", models.get("news_digest", default_article_model))
    elif normalized_type in {
        "editorial_narrative",
        "interleaved",
        "long_bullets",
        "news_digest",
        "structured",
    }:
        prompt_type = cast(SummarizationPromptType, normalized_type)
        default_model_spec = models.get(normalized_type, default_article_model)
    else:
        prompt_type = "structured"
        default_model_spec = models.get(normalized_type, default_article_model)

    return prompt_type, resolve_summarization_output_type(prompt_type), default_model_spec


def _is_context_length_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(hint in message for hint in CONTEXT_LENGTH_ERROR_HINTS)


def _extract_agent_output(result: Any) -> Any:
    if hasattr(result, "output"):
        return result.output
    if hasattr(result, "data"):
        return result.data
    raise AttributeError("Agent result missing output/data attribute")


def _clip_payload(payload: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0:
        return "", True
    if len(payload) <= max_chars:
        return payload, False

    marker = "\n\n[... CONTENT TRUNCATED ...]\n\n"
    if max_chars <= len(marker):
        return payload[:max_chars], True

    remaining = max_chars - len(marker)
    head_size = remaining // 2
    tail_size = remaining - head_size

    head = payload[:head_size].rstrip()
    tail = payload[-tail_size:].lstrip() if tail_size else ""
    clipped = f"{head}{marker}{tail}"

    if len(clipped) > max_chars:
        clipped = clipped[:max_chars]
    return clipped, True


DEFAULT_SUMMARIZATION_MODELS: dict[str, str] = {
    "news": "google:gemini-3.1-flash-lite-preview",
    "news_digest": "google:gemini-3.1-flash-lite-preview",
    "article": "openai:gpt-5.4-mini",
    "podcast": "openai:gpt-5.4-mini",
    "interleaved": "openai:gpt-5.4",
    "long_bullets": "openai:gpt-5.4",
    "editorial_narrative": "openai:gpt-5.4",
}


def _model_hint_from_spec(model_spec: str) -> tuple[str, str]:
    if ":" in model_spec:
        provider_prefix, hint = model_spec.split(":", 1)
        return provider_prefix, hint
    return "", model_spec


def _is_provider_precondition_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(hint in message for hint in PROVIDER_PRECONDITION_ERROR_HINTS)


def _is_provider_config_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(hint in message for hint in PROVIDER_CONFIG_ERROR_HINTS)


def _build_user_message(user_template: str, content_payload: str, title: str | None) -> str:
    """Build the user prompt for one summarization request."""
    content_body = content_payload
    if title:
        content_body = f"Title: {title}\n\n{content_body}"
    return user_template.format(content=content_body)


def _run_summarization_agent(
    *,
    model_spec: str,
    output_type: SummarizationOutputType,
    system_prompt: str,
    user_message: str,
) -> Any:
    """Run one typed summarization agent synchronously."""
    agent = get_basic_agent(model_spec, output_type, system_prompt)
    return agent.run_sync(user_message)


@dataclass
class ContentSummarizer:
    """Shared summarizer that routes to the right model based on content type."""

    default_models: dict[str, str] = field(default_factory=lambda: DEFAULT_SUMMARIZATION_MODELS)
    provider_hint: str | None = None
    model_hint: str | None = None
    _model_resolver: Callable[[str | None, str | None], tuple[str, str]] = resolve_model

    def summarize(
        self,
        content: str | bytes,
        content_type: str | ContentType,
        *,
        title: str | None = None,
        max_bullet_points: int = 6,
        max_quotes: int = 8,
        content_id: str | int | None = None,
        provider_override: str | None = None,
        model_hint: str | None = None,
    ) -> SummaryPayload | None:
        """Summarize arbitrary content with sensible defaults per content type."""
        normalized_type = _normalize_content_type(content_type)
        content_length = len(content) if content else 0

        try:
            payload = (
                content.decode("utf-8", errors="ignore")
                if isinstance(content, bytes)
                else content
            )
            if not payload:
                logger.warning("Empty summarization payload provided")
                return None

            raw_payload = payload
            payload, was_truncated = _clip_payload(payload, MAX_SUMMARIZATION_PAYLOAD_CHARS)
            if was_truncated:
                logger.warning(
                    "Content length %s exceeds max %s; truncating (head+tail) for summarization",
                    len(raw_payload),
                    MAX_SUMMARIZATION_PAYLOAD_CHARS,
                )

            prompt_type, output_type, default_model_spec = resolve_summarization_spec(
                normalized_type,
                self.default_models,
            )
            system_prompt, user_template = generate_summary_prompt(
                prompt_type,
                max_bullet_points,
                max_quotes,
            )
            user_message = _build_user_message(user_template, payload, title)

            default_provider_hint, default_model_hint = _model_hint_from_spec(default_model_spec)
            provider_to_use = provider_override or self.provider_hint or default_provider_hint
            model_hint_to_use = model_hint or self.model_hint or default_model_hint
            _, model_spec = self._model_resolver(provider_to_use, model_hint_to_use)

            try:
                result = _run_summarization_agent(
                    model_spec=model_spec,
                    output_type=output_type,
                    system_prompt=system_prompt,
                    user_message=user_message,
                )
            except Exception as model_error:  # noqa: BLE001
                if _is_context_length_error(model_error):
                    logger.error(
                        "Summarization context too long for content %s with model %s",
                        content_id or "unknown",
                        model_spec,
                    )
                elif _is_provider_precondition_error(model_error):
                    logger.error(
                        "Primary summarization model %s failed precondition for content %s",
                        model_spec,
                        content_id or "unknown",
                    )
                elif _is_provider_config_error(model_error):
                    logger.error(
                        "Summarization model %s is not configured for content %s",
                        model_spec,
                        content_id or "unknown",
                    )
                raise

            summary = _extract_agent_output(result)
            if summary is None:
                raise ValueError("Summarization agent returned no output")
            return _finalize_summary(cast(SummaryPayload, summary))
        except Exception as error:  # noqa: BLE001
            item_id = str(content_id or "unknown")
            logger.exception(
                "MISSING_SUMMARY: Summarization failed for content %s: %s. "
                "Content type: %s, Payload length: %s",
                item_id,
                error,
                normalized_type,
                content_length,
                extra={
                    "component": "llm_summarization",
                    "operation": "summarization",
                    "item_id": item_id,
                    "context_data": {
                        "content_type": normalized_type,
                        "payload_length": content_length,
                    },
                },
            )
            raise


_content_summarizer: ContentSummarizer | None = None


def get_content_summarizer() -> ContentSummarizer:
    """Return a shared ContentSummarizer instance."""
    global _content_summarizer
    if _content_summarizer is None:
        _content_summarizer = ContentSummarizer()
    return _content_summarizer
