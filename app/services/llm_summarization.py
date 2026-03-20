"""Shared summarization flow using pydantic-ai agents."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.models.metadata import (
    BulletedSummary,
    ContentQuote,
    ContentType,
    DailyNewsRollupSummary,
    EditorialNarrativeSummary,
    InterleavedSummary,
    InterleavedSummaryV2,
    NewsSummary,
    StructuredSummary,
)
from app.services.llm_agents import get_summarization_agent
from app.services.llm_models import resolve_model
from app.services.llm_prompts import generate_summary_prompt

logger = get_logger(__name__)

MAX_SUMMARIZATION_PAYLOAD_CHARS = 220_000
FALLBACK_SUMMARIZATION_PAYLOAD_CHARS = 120_000

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

EVENT_LOOP_BINDING_ERROR_HINTS: tuple[str, ...] = (
    "bound to a different event loop",
    "attached to a different loop",
)


@dataclass
class SummarizationRequest:
    """Request payload for summarizing content."""

    content: str
    content_type: str | ContentType
    model_spec: str
    title: str | None = None
    max_bullet_points: int = 6
    max_quotes: int = 8
    content_id: str | int | None = None


def _finalize_summary(
    summary: StructuredSummary
    | InterleavedSummary
    | InterleavedSummaryV2
    | BulletedSummary
    | EditorialNarrativeSummary
    | DailyNewsRollupSummary
    | NewsSummary,
    content_type: str | ContentType,
) -> (
    StructuredSummary
    | InterleavedSummary
    | InterleavedSummaryV2
    | BulletedSummary
    | EditorialNarrativeSummary
    | DailyNewsRollupSummary
    | NewsSummary
):
    """Apply lightweight cleanup to keep summaries consistent."""
    if isinstance(summary, StructuredSummary) and summary.quotes:
        filtered: list[ContentQuote] = [
            quote for quote in summary.quotes if len((quote.text or "").strip()) >= 10
        ]
        summary.quotes = filtered
    if isinstance(summary, InterleavedSummaryV2) and summary.quotes:
        filtered_quotes: list[ContentQuote] = [
            quote for quote in summary.quotes if len((quote.text or "").strip()) >= 10
        ]
        summary.quotes = filtered_quotes
    if isinstance(summary, EditorialNarrativeSummary) and summary.quotes:
        summary.quotes = [
            quote for quote in summary.quotes if len((quote.text or "").strip()) >= 10
        ]

    return summary


def _normalize_content_type(content_type: str | ContentType) -> str:
    return content_type.value if isinstance(content_type, ContentType) else str(content_type)


def _prompt_content_type(content_type: str) -> str:
    if content_type in {"article", "podcast"}:
        return "editorial_narrative"
    if content_type == "news":
        return "news_digest"
    return content_type


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
    "daily_news_rollup": "google:gemini-3.1-flash-lite-preview",
    "article": "openai:gpt-5.2",
    "podcast": "openai:gpt-5.2",
    "interleaved": "openai:gpt-5.2",
    "long_bullets": "openai:gpt-5.2",
    "editorial_narrative": "openai:gpt-5.2",
}

FALLBACK_SUMMARIZATION_MODEL = "google:gemini-2.5-flash"
CROSS_PROVIDER_FALLBACK_MODELS: tuple[str, ...] = (
    "openai:gpt-4o",
)


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


def _is_event_loop_binding_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(hint in message for hint in EVENT_LOOP_BINDING_ERROR_HINTS)


@dataclass
class ContentSummarizer:
    """Shared summarizer that routes to the right model based on content type."""

    default_models: dict[str, str] = field(default_factory=lambda: DEFAULT_SUMMARIZATION_MODELS)
    provider_hint: str | None = None
    model_hint: str | None = None
    _model_resolver: Callable[[str | None, str | None], tuple[str, str]] = resolve_model

    def summarize(
        self,
        content: str,
        content_type: str | ContentType,
        *,
        title: str | None = None,
        max_bullet_points: int = 6,
        max_quotes: int = 8,
        content_id: str | int | None = None,
        provider_override: str | None = None,
        model_hint: str | None = None,
    ) -> (
        StructuredSummary
        | InterleavedSummary
        | InterleavedSummaryV2
        | BulletedSummary
        | EditorialNarrativeSummary
        | DailyNewsRollupSummary
        | NewsSummary
        | None
    ):
        """Summarize arbitrary content with sensible defaults per content type."""
        normalized_type = _normalize_content_type(content_type)
        default_model_spec = self.default_models.get(
            normalized_type, self.default_models.get("article", FALLBACK_SUMMARIZATION_MODEL)
        )
        default_provider_hint, default_model_hint = _model_hint_from_spec(default_model_spec)

        provider_to_use = provider_override or self.provider_hint or default_provider_hint
        model_hint_to_use = model_hint or self.model_hint or default_model_hint

        _, model_spec = self._model_resolver(provider_to_use, model_hint_to_use)

        request = SummarizationRequest(
            content=content,
            content_type=normalized_type,
            model_spec=model_spec,
            title=title,
            max_bullet_points=max_bullet_points,
            max_quotes=max_quotes,
            content_id=content_id,
        )
        return summarize_content(request)

    def summarize_content(
        self,
        content: str,
        max_bullet_points: int = 6,
        max_quotes: int = 8,
        content_type: str | ContentType = "article",
        *,
        title: str | None = None,
        content_id: str | int | None = None,
        provider_override: str | None = None,
        model_hint: str | None = None,
    ) -> (
        StructuredSummary
        | InterleavedSummary
        | InterleavedSummaryV2
        | BulletedSummary
        | EditorialNarrativeSummary
        | DailyNewsRollupSummary
        | NewsSummary
        | None
    ):
        """Compatibility wrapper mirroring legacy service API."""
        return self.summarize(
            content=content,
            content_type=content_type,
            title=title,
            max_bullet_points=max_bullet_points,
            max_quotes=max_quotes,
            content_id=content_id,
            provider_override=provider_override,
            model_hint=model_hint,
        )


_content_summarizer: ContentSummarizer | None = None


def get_content_summarizer() -> ContentSummarizer:
    """Return a shared ContentSummarizer instance."""
    global _content_summarizer
    if _content_summarizer is None:
        _content_summarizer = ContentSummarizer()
    return _content_summarizer


def summarize_content(
    request: SummarizationRequest,
) -> (
    StructuredSummary
    | InterleavedSummary
    | InterleavedSummaryV2
    | BulletedSummary
    | EditorialNarrativeSummary
    | DailyNewsRollupSummary
    | NewsSummary
    | None
):
    """Generate a structured summary via pydantic-ai.

    Args:
        request: SummarizationRequest containing content, model spec, and limits.

    Returns:
        Parsed summary payload (Bulleted/Structured/Interleaved/News), or None on failure.
    """
    try:
        payload = (
            request.content.decode("utf-8", errors="ignore")
            if isinstance(request.content, bytes)
            else request.content
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

        ct = request.content_type
        content_type_value = ct.value if isinstance(ct, ContentType) else str(ct)

        prompt_content_type = _prompt_content_type(content_type_value)

        system_prompt, user_template = generate_summary_prompt(
            prompt_content_type, request.max_bullet_points, request.max_quotes
        )

        def _build_user_message(content_payload: str) -> str:
            content_body = content_payload
            if request.title:
                content_body = f"Title: {request.title}\n\n{content_body}"
            return user_template.format(content=content_body)

        def _run_with_model(model_spec: str, content_payload: str) -> Any:
            message = _build_user_message(content_payload)
            agent = get_summarization_agent(model_spec, prompt_content_type, system_prompt)
            try:
                return agent.run_sync(message)
            except RuntimeError as loop_error:
                if not _is_event_loop_binding_error(loop_error):
                    raise
                logger.warning(
                    "Event-loop binding error for content %s with model %s; retrying in isolated "
                    "thread",
                    request.content_id or "unknown",
                    model_spec,
                )

                def _run_in_thread() -> Any:
                    retry_agent = get_summarization_agent(
                        model_spec,
                        prompt_content_type,
                        system_prompt,
                    )
                    return retry_agent.run_sync(message)

                with ThreadPoolExecutor(max_workers=1) as executor:
                    return executor.submit(_run_in_thread).result()

        def _run_with_context_fallback(model_spec: str) -> Any:
            try:
                return _run_with_model(model_spec, payload)
            except Exception as model_error:  # noqa: BLE001
                if not _is_context_length_error(model_error):
                    raise

                if model_spec != FALLBACK_SUMMARIZATION_MODEL:
                    logger.warning(
                        "Summarization context too long for content %s with model %s; "
                        "retrying with fallback model %s",
                        request.content_id or "unknown",
                        model_spec,
                        FALLBACK_SUMMARIZATION_MODEL,
                    )
                    return _run_with_context_fallback(FALLBACK_SUMMARIZATION_MODEL)

                fallback_payload, _ = _clip_payload(
                    raw_payload,
                    FALLBACK_SUMMARIZATION_PAYLOAD_CHARS,
                )
                logger.warning(
                    "Fallback model %s exceeded context; clipping to %s chars",
                    FALLBACK_SUMMARIZATION_MODEL,
                    FALLBACK_SUMMARIZATION_PAYLOAD_CHARS,
                )
                return _run_with_model(model_spec, fallback_payload)

        try:
            result = _run_with_context_fallback(request.model_spec)
        except Exception as primary_error:  # noqa: BLE001
            if not _is_provider_precondition_error(primary_error):
                raise

            logger.warning(
                "Primary summarization model %s failed precondition for content %s; "
                "trying cross-provider fallbacks",
                request.model_spec,
                request.content_id or "unknown",
            )
            last_error: Exception = primary_error
            attempted_specs = {request.model_spec}

            for fallback_model_spec in CROSS_PROVIDER_FALLBACK_MODELS:
                if fallback_model_spec in attempted_specs:
                    continue
                attempted_specs.add(fallback_model_spec)
                try:
                    result = _run_with_context_fallback(fallback_model_spec)
                    break
                except Exception as fallback_error:  # noqa: BLE001
                    last_error = fallback_error
                    if _is_provider_precondition_error(fallback_error):
                        logger.warning(
                            "Cross-provider fallback model %s also failed precondition",
                            fallback_model_spec,
                        )
                        continue
                    if _is_provider_config_error(fallback_error):
                        logger.warning(
                            "Skipping fallback model %s due to provider configuration: %s",
                            fallback_model_spec,
                            fallback_error,
                        )
                        continue
                    raise
            else:
                raise last_error

        summary = _extract_agent_output(result)
        if summary is None:
            return None
        return _finalize_summary(summary, request.content_type)
    except Exception as error:  # noqa: BLE001
        item_id = str(request.content_id or "unknown")
        logger.exception(
            "MISSING_SUMMARY: Summarization failed for content %s: %s. "
            "Model: %s, Content type: %s, Payload length: %s",
            item_id,
            error,
            request.model_spec,
            request.content_type,
            len(request.content) if request.content else 0,
            extra={
                "component": "llm_summarization",
                "operation": "summarization",
                "item_id": item_id,
                "context_data": {
                    "model_spec": request.model_spec,
                    "content_type": str(request.content_type),
                    "payload_length": len(request.content) if request.content else 0,
                },
            },
        )
        return None
