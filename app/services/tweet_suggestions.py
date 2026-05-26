"""Generate tweet suggestions for content items via configured LLM providers."""

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator
from pydantic_ai import Agent, ModelRetry, PromptedOutput
from pydantic_ai.settings import ModelSettings
from tenacity import RetryCallState, retry, stop_after_attempt, wait_exponential

from app.constants import TWEET_MODELS, TWEET_SUGGESTION_MODEL
from app.core.logging import get_logger
from app.models.contracts import ContentType, SummaryKind, SummaryVersion
from app.models.domain.content import ContentData
from app.models.metadata.summary_contracts import parse_summary_version, resolve_summary_kind
from app.services.llm_agents import get_basic_agent
from app.services.llm_models import resolve_model
from app.services.llm_prompts import get_tweet_generation_prompt, length_to_char_range
from app.utils.json_repair import try_repair_truncated_json
from app.utils.url_utils import normalize_http_url

logger = get_logger(__name__)

# Model for tweet generation
TWEET_MODEL = TWEET_SUGGESTION_MODEL


class TweetSuggestionLLM(BaseModel):
    """Structured tweet suggestion returned by the LLM."""

    id: int = Field(..., ge=1, le=3)
    text: str = Field(..., min_length=1)
    style_label: str | None = Field(default=None)


class TweetSuggestionsPayload(BaseModel):
    """Structured payload returned by the LLM."""

    suggestions: list[TweetSuggestionLLM] = Field(
        ..., min_length=3, max_length=3, description="Exactly three tweet suggestions"
    )

    @model_validator(mode="after")
    def ensure_three_suggestions(self) -> "TweetSuggestionsPayload":
        """Ensure exactly three suggestions are returned."""
        if len(self.suggestions) != 3:
            raise ValueError(f"Expected 3 suggestions, got {len(self.suggestions)}")
        return self


@dataclass
class TweetSuggestionData:
    """Internal data structure for a single tweet suggestion."""

    id: int
    text: str
    style_label: str | None


@dataclass
class TweetSuggestionsResult:
    """Result from tweet suggestion generation."""

    content_id: int
    creativity: int
    length: str
    model: str
    suggestions: list[TweetSuggestionData]


def creativity_to_temperature(creativity: int) -> float:
    """
    Map creativity level (1-10) to temperature for LLM.

    Args:
        creativity: Integer 1-10

    Returns:
        Float temperature value (0.1 to 1.0)
    """
    # Clamp to valid range
    creativity = max(1, min(10, creativity))
    # Linear mapping: 1 -> 0.1, 10 -> 1.0
    return 0.1 + (creativity - 1) * 0.1


def _extract_content_context(content: ContentData) -> dict[str, str]:
    """
    Extract relevant context from ContentData for tweet generation.

    Args:
        content: The ContentData object

    Returns:
        Dictionary with title, source, platform, url, summary, key_points,
        quotes, questions, counter_arguments
    """
    title = content.display_title
    source = content.source or "Unknown"
    platform = content.platform or "web"

    # Get URL
    url = str(content.url)
    if content.content_type == ContentType.ARTICLE:
        # Prefer canonical URL, fallback to legacy metadata
        normalized = normalize_http_url(str(content.url))
        if normalized:
            url = normalized
        else:
            final_url = content.metadata.get("final_url_after_redirects")
            if final_url:
                url = final_url
    elif content.content_type == ContentType.NEWS:
        normalized = normalize_http_url(str(content.url))
        if normalized:
            url = normalized
        else:
            # Fallback to legacy metadata
            article_meta = content.metadata.get("article")
            if isinstance(article_meta, dict):
                article_url = article_meta.get("url")
                if article_url:
                    url = str(article_url)

    # Get summary text
    summary = ""
    key_points: list[str] = []
    quotes: list[str] = []
    questions: list[str] = []
    counter_arguments: list[str] = []

    summary_data = content.metadata.get("summary")
    summary_kind = resolve_summary_kind(summary_data, content.metadata.get("summary_kind"))
    summary_version = parse_summary_version(content.metadata.get("summary_version"))
    if isinstance(summary_data, dict):
        # Check if it's a StructuredSummary, InterleavedSummary, or NewsSummary
        if summary_kind == SummaryKind.LONGFORM_ARTIFACT:
            artifact = summary_data.get("artifact")
            payload = artifact.get("payload") if isinstance(artifact, dict) else None
            summary = (
                summary_data.get("one_line")
                or (payload.get("overview") if isinstance(payload, dict) else None)
                or ""
            )
            if isinstance(payload, dict):
                for point in payload.get("key_points", [])[:5]:
                    if isinstance(point, dict):
                        text = " — ".join(
                            part
                            for part in (
                                str(point.get("heading") or "").strip(),
                                str(point.get("content") or "").strip(),
                            )
                            if part
                        )
                        if text:
                            key_points.append(text)
                for quote in payload.get("quotes", [])[:3]:
                    if isinstance(quote, dict) and quote.get("text"):
                        quotes.append(str(quote["text"]))
        elif summary_kind == SummaryKind.LONG_STRUCTURED:
            summary = summary_data.get("overview", "")
        elif summary_kind == SummaryKind.LONG_INTERLEAVED:
            summary = summary_data.get("hook") or summary_data.get("takeaway", "")
        elif summary_kind == SummaryKind.LONG_EDITORIAL_NARRATIVE:
            summary = summary_data.get("editorial_narrative", "")
        elif summary_kind == SummaryKind.SHORT_NEWS:
            summary = summary_data.get("summary", "")
        elif "summary" in summary_data:
            # Legacy fallback when `summary_kind` was not persisted.
            summary = summary_data.get("summary", "")

        # Get bullet points / key points
        if summary_kind == SummaryKind.LONGFORM_ARTIFACT:
            bullet_points = []
        elif summary_kind == SummaryKind.LONG_INTERLEAVED and summary_version == SummaryVersion.V2:
            bullet_points = summary_data.get("key_points", [])
        else:
            bullet_points = summary_data.get("key_points") or summary_data.get("bullet_points", [])
        if bullet_points:
            # Handle both dict and string formats
            for point in bullet_points[:5]:  # Limit to 5 points
                if isinstance(point, dict):
                    key_points.append(point.get("text", ""))
                elif isinstance(point, str):
                    key_points.append(point)
        else:
            if summary_kind == SummaryKind.LONG_INTERLEAVED:
                insights = summary_data.get("insights", [])
                for insight in insights[:5]:
                    if isinstance(insight, dict):
                        key_points.append(insight.get("insight", ""))
            elif summary_kind == SummaryKind.LONG_EDITORIAL_NARRATIVE:
                editorial_points = summary_data.get("key_points", [])
                for point in editorial_points[:5]:
                    if isinstance(point, dict):
                        key_points.append(point.get("point", ""))

        # Get quotes (for articles with StructuredSummary)
        raw_quotes = summary_data.get("quotes", [])
        if raw_quotes:
            for quote in raw_quotes[:3]:  # Limit to 3 quotes
                if isinstance(quote, dict):
                    quote_text = quote.get("text", "")
                    if quote_text:
                        quotes.append(quote_text)
                elif isinstance(quote, str):
                    quotes.append(quote)
        elif summary_kind == SummaryKind.LONG_INTERLEAVED:
            insights = summary_data.get("insights", [])
            for insight in insights[:3]:
                if isinstance(insight, dict):
                    quote_text = insight.get("supporting_quote")
                    if quote_text:
                        quotes.append(quote_text)

        # Get questions
        raw_questions = summary_data.get("questions", [])
        if raw_questions:
            for q in raw_questions[:3]:  # Limit to 3 questions
                if isinstance(q, str):
                    questions.append(q)

        # Get counter-arguments
        raw_counter_args = summary_data.get("counter_arguments", [])
        if raw_counter_args:
            for arg in raw_counter_args[:3]:  # Limit to 3 counter-arguments
                if isinstance(arg, str):
                    counter_arguments.append(arg)

    # Fallback to short_summary if no overview
    if not summary:
        summary = content.short_summary or content.summary or ""

    return {
        "title": title,
        "source": source,
        "platform": platform,
        "url": url,
        "summary": summary,
        "key_points": "\n".join(f"- {p}" for p in key_points) if key_points else "N/A",
        "quotes": "\n".join(f'- "{q}"' for q in quotes) if quotes else "N/A",
        "questions": "\n".join(f"- {q}" for q in questions) if questions else "N/A",
        "counter_arguments": (
            "\n".join(f"- {arg}" for arg in counter_arguments) if counter_arguments else "N/A"
        ),
    }


def _extract_json_from_response(content: str) -> str:
    """Extract JSON from response, handling markdown code blocks."""
    # Remove markdown code blocks if present
    if "```json" in content:
        start = content.find("```json") + 7
        end = content.find("```", start)
        if end != -1:
            content = content[start:end].strip()
    elif "```" in content:
        start = content.find("```") + 3
        end = content.find("```", start)
        if end != -1:
            content = content[start:end].strip()

    return content.strip()


def _parse_suggestions_response(raw_response: str) -> list[dict[str, Any]] | None:
    """
    Parse the LLM response into suggestion dictionaries.

    Args:
        raw_response: Raw string response from LLM

    Returns:
        List of suggestion dicts or None if parsing fails
    """
    try:
        cleaned = _extract_json_from_response(raw_response)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            # Try to repair truncated JSON
            repaired = try_repair_truncated_json(cleaned)
            if repaired:
                data = json.loads(repaired)
            else:
                logger.warning("Could not parse tweet suggestions JSON: %s", exc)
                return None

        # Extract suggestions array
        suggestions = data.get("suggestions", [])
        if not isinstance(suggestions, list) or len(suggestions) != 3:
            logger.warning("Expected exactly 3 suggestions, got %d", len(suggestions))
            return None

        return suggestions

    except Exception as e:
        logger.exception(
            "Failed to parse tweet suggestions: %s",
            e,
            extra={
                "component": "tweet_suggestions",
                "operation": "parse_tweet_suggestions",
                "item_id": "unknown",
                "context_data": {"raw_response": raw_response[:500]},
            },
        )
        return None


def _validate_and_truncate_tweets(
    suggestions: list[dict[str, Any]],
    max_chars: int = 400,
) -> list[TweetSuggestionData]:
    """
    Validate suggestions and truncate if over max_chars.

    Args:
        suggestions: List of suggestion dicts from LLM
        max_chars: Maximum character limit for tweets

    Returns:
        List of validated TweetSuggestionData
    """
    result = []
    for i, suggestion in enumerate(suggestions, start=1):
        text = suggestion.get("text", "")
        style_label = suggestion.get("style_label")

        # Truncate if too long
        if len(text) > max_chars:
            logger.warning("Tweet %d exceeds %d chars (%d), truncating", i, max_chars, len(text))
            text = text[: max_chars - 3] + "..."

        result.append(
            TweetSuggestionData(
                id=suggestion.get("id", i),
                text=text,
                style_label=style_label,
            )
        )

    return result


def _model_hint_from_spec(model_spec: str) -> tuple[str | None, str]:
    """Split a model spec into provider prefix and hint."""
    if ":" in model_spec:
        provider_prefix, hint = model_spec.split(":", 1)
        return provider_prefix, hint
    return None, model_spec


def _is_google_model_spec(model_spec: str) -> bool:
    """Return whether the model spec targets Gemini/Google."""
    provider_prefix, model_hint = _model_hint_from_spec(model_spec)
    return provider_prefix in {"google", "google-gla"} or model_hint.startswith("gemini")


def _tweet_output_type_for_model(model_spec: str) -> Any:
    """Choose the structured-output mode that works reliably for the provider."""
    if _is_google_model_spec(model_spec):
        return TweetSuggestionsPayload

    return PromptedOutput(
        TweetSuggestionsPayload,
        name="tweet_suggestions",
        description="Exactly three tweet suggestions as a JSON object.",
    )


def _coerce_tweet_payload(output: Any) -> TweetSuggestionsPayload:
    """Normalize pydantic-ai output into the expected tweet payload."""
    if isinstance(output, TweetSuggestionsPayload):
        return output
    if isinstance(output, dict):
        return TweetSuggestionsPayload.model_validate(output)
    if isinstance(output, str):
        suggestions = _parse_suggestions_response(output)
        if suggestions is None:
            raise ValueError("Tweet suggestions response did not contain valid JSON")
        return TweetSuggestionsPayload.model_validate({"suggestions": suggestions})
    raise ValueError(f"Unexpected tweet suggestions output type: {type(output).__name__}")


def _log_generation_failure(retry_state: RetryCallState) -> None:
    """Log final generation failure after all retries."""
    content = retry_state.args[1] if len(retry_state.args) > 1 else None
    content_id = getattr(content, "id", "unknown")
    creativity = None
    if retry_state.kwargs:
        creativity = retry_state.kwargs.get("creativity")
    elif len(retry_state.args) > 3:
        creativity = retry_state.args[3]

    error = retry_state.outcome.exception() if retry_state.outcome else None
    if error:
        logger.exception(
            "Tweet generation failed after %d attempts | content_id=%s error=%s",
            retry_state.attempt_number,
            content_id,
            error,
            extra={
                "component": "tweet_suggestions",
                "operation": "tweet_generation_failure",
                "item_id": str(content_id),
                "context_data": {"creativity": creativity},
            },
        )
    else:
        logger.error(
            "Tweet generation failed after %d attempts | content_id=%s",
            retry_state.attempt_number,
            content_id,
        )
    return None


class TweetSuggestionService:
    """Service for generating tweet suggestions using various LLM providers."""

    def __init__(self):
        self.default_model = TWEET_MODEL
        logger.info("Initialized TweetSuggestionService with default model %s", self.default_model)

    def _get_model_for_provider(self, provider: str | None) -> str:
        """Get the model name for a given provider."""
        model_spec = self.default_model
        if provider and provider in TWEET_MODELS:
            model_spec = TWEET_MODELS[provider]

        default_provider_hint, model_hint = _model_hint_from_spec(model_spec)
        provider_hint = provider or default_provider_hint
        _, resolved_spec = resolve_model(provider_hint, model_hint)
        return resolved_spec

    def _build_agent(
        self, system_prompt: str, model_name: str
    ) -> Agent[None, TweetSuggestionsPayload]:
        """Create a configured pydantic-ai agent for tweet suggestions."""
        return get_basic_agent(
            model_spec=model_name,
            output_type=_tweet_output_type_for_model(model_name),
            system_prompt=system_prompt,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry_error_callback=_log_generation_failure,
    )
    def generate_suggestions(
        self,
        content: ContentData,
        message: str | None = None,
        creativity: int = 5,
        length: str = "medium",
        llm_provider: str | None = None,
    ) -> TweetSuggestionsResult | None:
        """
        Generate tweet suggestions for content.

        Args:
            content: The ContentData to generate tweets for.
            message: Optional user guidance/tweak message.
            creativity: Creativity level 1-10.
            length: Tweet length preference ("short", "medium", "long").
            llm_provider: Optional LLM provider (openai, anthropic, google).

        Returns:
            TweetSuggestionsResult with 3 suggestions, or None on failure.
        """
        content_id = content.id or 0
        model_name = self._get_model_for_provider(llm_provider)

        try:
            # Extract context from content
            context = _extract_content_context(content)

            # Get prompts with length preference
            system_message, user_template = get_tweet_generation_prompt(
                creativity=creativity,
                user_message=message,
                length=length,
            )

            # Format user message with content context
            user_message = user_template.format(**context)

            # Calculate temperature from creativity
            temperature = creativity_to_temperature(creativity)

            # Run LLM via pydantic-ai
            agent = self._build_agent(system_prompt=system_message, model_name=model_name)
            run_result = agent.run_sync(
                user_message,
                model_settings=ModelSettings(
                    max_tokens=2000,
                    temperature=temperature,
                ),
            )

            payload = _coerce_tweet_payload(run_result.output)

            # Validate and truncate based on length preference
            _, max_chars = length_to_char_range(length)
            suggestions = _validate_and_truncate_tweets(
                [suggestion.model_dump() for suggestion in payload.suggestions],
                max_chars=max_chars,
            )
            if len(suggestions) != 3:
                raise ValueError(f"Expected 3 suggestions after validation, got {len(suggestions)}")

            # Log usage metrics
            usage = run_result.usage()
            if usage:
                logger.info(
                    "Tweet generation - content_id: %d, creativity: %d, model: %s, "
                    "input_tokens: %d, output_tokens: %d",
                    content_id,
                    creativity,
                    model_name,
                    usage.input_tokens,
                    usage.output_tokens,
                )

            return TweetSuggestionsResult(
                content_id=content_id,
                creativity=creativity,
                length=length,
                model=model_name,
                suggestions=suggestions,
            )

        except (ModelRetry, ValidationError, ValueError) as e:
            logger.warning(
                "LLM validation error generating tweets | content_id=%s model=%s error=%s",
                content_id,
                model_name,
                e,
            )
            raise
        except Exception as e:
            logger.error(
                "Unexpected error generating tweets | content_id=%s model=%s error=%s",
                content_id,
                model_name,
                e,
            )
            raise


# Module-level service instance (lazy initialization)
_service_instance: TweetSuggestionService | None = None


def get_tweet_suggestion_service() -> TweetSuggestionService:
    """Get or create the TweetSuggestionService singleton."""
    global _service_instance
    if _service_instance is None:
        _service_instance = TweetSuggestionService()
    return _service_instance


def generate_tweet_suggestions(
    content: ContentData,
    message: str | None = None,
    creativity: int = 5,
    length: str = "medium",
    llm_provider: str | None = None,
) -> TweetSuggestionsResult | None:
    """
    Convenience function to generate tweet suggestions.

    Args:
        content: The ContentData to generate tweets for
        message: Optional user guidance/tweak message
        creativity: Creativity level 1-10
        length: Tweet length preference ("short", "medium", "long")
        llm_provider: Optional LLM provider (openai, anthropic, google)

    Returns:
        TweetSuggestionsResult with 3 suggestions, or None on failure
    """
    service = get_tweet_suggestion_service()
    return service.generate_suggestions(content, message, creativity, length, llm_provider)
