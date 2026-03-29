#!/usr/bin/env python3
"""Generate a static HTML eval report with side-by-side model outputs."""

from __future__ import annotations

# ruff: noqa: E501
import argparse
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

# Add project root to import path when running as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, Field

from app.core.db import get_db, init_db
from app.core.logging import get_logger, setup_logging
from app.core.settings import get_settings
from app.models.schema import Content
from app.services.admin_eval import (
    EVAL_MODEL_LABELS,
    EVAL_MODEL_SPECS,
    MAX_EVAL_INPUT_CHARS,
    build_eval_source_payload,
    select_eval_samples,
)
from app.services.llm_agents import get_basic_agent
from app.services.llm_prompts import generate_summary_prompt
from app.services.llm_summarization import resolve_summarization_output_type

logger = get_logger(__name__)

EvalContentType = Literal["article", "podcast", "news"]
LongformTemplate = Literal[
    "long_bullets_v1",
    "interleaved_v2",
    "structured_v1",
    "editorial_narrative_v1",
]
PromptType = Literal[
    "long_bullets",
    "interleaved",
    "structured",
    "news_digest",
    "editorial_narrative",
]

ESTIMATED_CHARS_PER_TOKEN = 4


class EditorialQuote(BaseModel):
    """Quote snippet used by editorial narrative summaries."""

    text: str = Field(min_length=10)
    attribution: str | None = None


class EditorialKeyPoint(BaseModel):
    """Key point entry used in editorial narrative summaries."""

    point: str = Field(min_length=10)


class EditorialNarrativeSummary(BaseModel):
    """Custom summary schema for editorial narrative prompt tests."""

    title: str = Field(min_length=10, max_length=140)
    editorial_narrative: str = Field(min_length=180)
    quotes: list[EditorialQuote] = Field(min_length=2, max_length=6)
    key_points: list[EditorialKeyPoint] = Field(min_length=4, max_length=12)
    classification: Literal["to_read", "skip"]
    summarization_date: str


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.

    Returns:
        Parsed command line arguments.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Run eval model comparisons and build a static HTML report with model outputs, "
            "tokens, and latency."
        )
    )
    parser.add_argument(
        "--content-types",
        type=str,
        default="article,podcast,news",
        help="Comma-separated content types.",
    )
    parser.add_argument(
        "--models",
        type=str,
        default=",".join(EVAL_MODEL_SPECS.keys()),
        help="Comma-separated model aliases from admin eval.",
    )
    parser.add_argument(
        "--longform-template",
        type=str,
        choices=[
            "long_bullets_v1",
            "interleaved_v2",
            "structured_v1",
            "editorial_narrative_v1",
        ],
        default="long_bullets_v1",
        help="Built-in long-form prompt template for article/podcast.",
    )
    parser.add_argument("--recent-pool-size", type=int, default=200)
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--content-ids",
        type=str,
        default=None,
        help="Optional explicit content IDs, comma-separated.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=60,
        help="Per-model call timeout passed to pydantic-ai model settings.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=1,
        help="Extra retries per model call after the first attempt fails.",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=1.5,
        help="Base backoff for retries (multiplied by attempt number).",
    )
    parser.add_argument(
        "--parallel-model-calls",
        type=int,
        default=1,
        help="Deprecated. Parallel model calls are disabled; runs always execute sequentially.",
    )
    parser.add_argument(
        "--max-input-chars",
        type=int,
        default=MAX_EVAL_INPUT_CHARS,
        help="Clip content text above this size before prompting.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Optional exact output directory; defaults to outputs/eval_html/<timestamp>.",
    )
    parser.add_argument(
        "--custom-longform-system-prompt-file",
        type=str,
        default=None,
        help="Optional file path for custom article/podcast system prompt.",
    )
    parser.add_argument(
        "--custom-longform-user-template-file",
        type=str,
        default=None,
        help="Optional file path for custom article/podcast user template containing {content}.",
    )
    parser.add_argument(
        "--custom-longform-output-type",
        type=str,
        choices=["long_bullets", "interleaved", "structured", "editorial_narrative"],
        default="long_bullets",
        help="Output schema type to use when custom longform prompts are configured.",
    )
    parser.add_argument(
        "--custom-news-system-prompt-file",
        type=str,
        default=None,
        help="Optional file path for custom news system prompt.",
    )
    parser.add_argument(
        "--custom-news-user-template-file",
        type=str,
        default=None,
        help="Optional file path for custom news user template containing {content}.",
    )
    parser.add_argument(
        "--custom-news-output-type",
        type=str,
        choices=[
            "news_digest",
            "long_bullets",
            "interleaved",
            "structured",
            "editorial_narrative",
        ],
        default="news_digest",
        help="Output schema type to use when custom news prompts are configured.",
    )
    return parser.parse_args()


def parse_csv_list(raw_value: str) -> list[str]:
    """Parse a comma-separated string to a deduplicated list.

    Args:
        raw_value: Comma-separated text value.

    Returns:
        List of unique values in original order.
    """
    values = [item.strip() for item in raw_value.split(",") if item.strip()]
    return list(dict.fromkeys(values))


def parse_content_ids(raw_value: str | None) -> list[int]:
    """Parse optional comma-separated content IDs.

    Args:
        raw_value: Raw comma-separated content IDs.

    Returns:
        Parsed integer content IDs.
    """
    if not raw_value:
        return []
    values = parse_csv_list(raw_value)
    return [int(item) for item in values]


def validate_content_types(content_types: list[str]) -> list[EvalContentType]:
    """Validate content types.

    Args:
        content_types: Parsed content type strings.

    Returns:
        Validated content type list.

    Raises:
        ValueError: If any content type is unsupported.
    """
    allowed = {"article", "podcast", "news"}
    invalid = [item for item in content_types if item not in allowed]
    if invalid:
        raise ValueError(f"Unsupported content types: {', '.join(invalid)}")
    if not content_types:
        raise ValueError("At least one content type is required")
    return content_types  # type: ignore[return-value]


def validate_models(models: list[str]) -> list[str]:
    """Validate model aliases.

    Args:
        models: Parsed model alias list.

    Returns:
        Validated model alias list.

    Raises:
        ValueError: If unknown model aliases were provided.
    """
    unknown = [alias for alias in models if alias not in EVAL_MODEL_SPECS]
    if unknown:
        raise ValueError(f"Unknown model aliases: {', '.join(unknown)}")
    if not models:
        raise ValueError("At least one model alias is required")
    return models


def ensure_prompt_override_pair(
    system_path: str | None,
    user_path: str | None,
    label: str,
) -> None:
    """Validate that custom prompt files are provided in system/user pairs.

    Args:
        system_path: Optional system prompt file path.
        user_path: Optional user template file path.
        label: Prompt group label for error messages.

    Raises:
        ValueError: If one of the files is missing.
    """
    if bool(system_path) != bool(user_path):
        raise ValueError(
            f"{label} custom prompts require both system and user template files together."
        )


def load_prompt_file(path: str) -> str:
    """Load a UTF-8 prompt file from disk.

    Args:
        path: File path.

    Returns:
        File contents.

    Raises:
        ValueError: If the file is empty.
    """
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Prompt file is empty: {path}")
    return text


def resolve_output_directory(output_dir: str | None) -> Path:
    """Resolve report output directory.

    Args:
        output_dir: Optional user-provided output path.

    Returns:
        Directory path where report files should be written.
    """
    if output_dir:
        path = Path(output_dir)
    else:
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        path = Path("outputs") / "eval_html" / timestamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def clip_eval_input(text: str, max_input_chars: int) -> str:
    """Clip long content text to keep requests bounded.

    Args:
        text: Source input text.
        max_input_chars: Maximum characters to keep.

    Returns:
        Possibly clipped input text.
    """
    if len(text) <= max_input_chars:
        return text

    marker = "\n\n[... CONTENT TRUNCATED FOR REPORT ...]\n\n"
    remaining = max_input_chars - len(marker)
    if remaining <= 0:
        return text[:max_input_chars]

    head_size = remaining // 2
    tail_size = remaining - head_size
    return f"{text[:head_size].rstrip()}{marker}{text[-tail_size:].lstrip()}"


def estimate_tokens_from_chars(char_count: int) -> int:
    """Estimate token count from character count.

    Args:
        char_count: Character length.

    Returns:
        Approximate token count.
    """
    if char_count <= 0:
        return 0
    return math.ceil(char_count / ESTIMATED_CHARS_PER_TOKEN)


def coerce_int(value: object | None) -> int | None:
    """Safely coerce values to integers.

    Args:
        value: Candidate value.

    Returns:
        Parsed integer or ``None``.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_usage(result: Any) -> dict[str, int | None]:
    """Extract token usage values from a pydantic-ai result.

    Args:
        result: Pydantic-ai run result.

    Returns:
        Token usage dictionary.
    """
    try:
        usage = result.usage()
    except Exception:  # noqa: BLE001
        usage = None

    if not usage:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None}

    input_tokens = coerce_int(
        getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
    )
    output_tokens = coerce_int(
        getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None)
    )
    total_tokens = coerce_int(getattr(usage, "total_tokens", None))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def extract_result_payload(result: Any) -> dict[str, Any]:
    """Extract JSON-serializable output payload from pydantic-ai result.

    Args:
        result: Pydantic-ai run result.

    Returns:
        Dict payload for report rendering.
    """
    output = getattr(result, "output", None)
    if output is None:
        output = getattr(result, "data", None)
    if output is None:
        raise ValueError("Model result did not include output payload")
    if hasattr(output, "model_dump"):
        return output.model_dump(mode="json", exclude_none=True)
    if isinstance(output, dict):
        return output
    raise ValueError("Model result payload is not JSON serializable")


def resolve_builtin_prompt_settings(
    content_type: EvalContentType,
    longform_template: LongformTemplate,
) -> tuple[PromptType, int, int]:
    """Resolve default prompt settings matching eval behavior.

    Args:
        content_type: Content type for the row.
        longform_template: Long-form template selection.

    Returns:
        Tuple of prompt type, max bullet points, and max quotes.
    """
    if content_type == "news":
        return "news_digest", 4, 0

    if longform_template == "interleaved_v2":
        return "interleaved", 8, 8
    if longform_template == "structured_v1":
        return "structured", 12, 8
    if longform_template == "editorial_narrative_v1":
        return "editorial_narrative", 10, 4
    return "long_bullets", 30, 3


def generate_editorial_narrative_prompt(max_key_points: int, max_quotes: int) -> tuple[str, str]:
    """Generate the built-in editorial narrative prompt.

    Args:
        max_key_points: Maximum number of key points.
        max_quotes: Maximum number of quoted snippets.

    Returns:
        System prompt and user message template.
    """
    system_prompt = f"""You are an expert editorial analyst writing a narrative-first summary.

Return a JSON object with exactly these fields:
{{
  "title": "Descriptive title (max 140 chars)",
  "editorial_narrative": "2-4 paragraph editorial narrative. Start with a clear thesis, summarize the article, and weave in direct quotes naturally.",
  "quotes": [
    {{
      "text": "Direct quote from the source (min 10 chars)",
      "attribution": "Who said it (optional)"
    }}
  ],
  "key_points": [
    {{
      "point": "Concrete key point"
    }}
  ],
  "classification": "to_read" | "skip",
  "summarization_date": "ISO 8601 timestamp"
}}

Guidelines:
- The narrative must read like an editorial brief, not bullets.
- Include 2-{max_quotes} direct quotes in both the narrative text and quotes array.
- After the narrative, key_points should list 4-{max_key_points} concrete takeaways.
- Preserve technical terms accurately and avoid spelling mistakes.
- Never include markdown or extra fields outside the JSON object.
"""
    user_template = "Content:\n\n{content}"
    return system_prompt, user_template


def resolve_prompt_for_source(
    *,
    content_type: EvalContentType,
    longform_template: LongformTemplate,
    custom_longform_system_prompt: str | None,
    custom_longform_user_template: str | None,
    custom_longform_output_type: PromptType,
    custom_news_system_prompt: str | None,
    custom_news_user_template: str | None,
    custom_news_output_type: PromptType,
) -> tuple[str, str, PromptType]:
    """Resolve system prompt, user template, and output schema prompt type.

    Args:
        content_type: Content type for the current row.
        longform_template: Built-in template selector.
        custom_longform_system_prompt: Optional custom longform system prompt.
        custom_longform_user_template: Optional custom longform user template.
        custom_longform_output_type: Output schema type for custom longform prompt.
        custom_news_system_prompt: Optional custom news system prompt.
        custom_news_user_template: Optional custom news user template.
        custom_news_output_type: Output schema type for custom news prompt.

    Returns:
        Tuple of system prompt, user template, and prompt type.
    """
    if content_type == "news" and custom_news_system_prompt and custom_news_user_template:
        return custom_news_system_prompt, custom_news_user_template, custom_news_output_type

    if content_type != "news" and custom_longform_system_prompt and custom_longform_user_template:
        return (
            custom_longform_system_prompt,
            custom_longform_user_template,
            custom_longform_output_type,
        )

    prompt_type, max_bullet_points, max_quotes = resolve_builtin_prompt_settings(
        content_type,
        longform_template,
    )
    if prompt_type == "editorial_narrative":
        system_prompt, user_template = generate_editorial_narrative_prompt(
            max_key_points=max_bullet_points,
            max_quotes=max_quotes,
        )
    else:
        system_prompt, user_template = generate_summary_prompt(
            prompt_type,
            max_bullet_points=max_bullet_points,
            max_quotes=max_quotes,
        )
    return system_prompt, user_template, prompt_type


def resolve_available_models(models: list[str]) -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
    """Filter configured models by provider API key availability.

    Args:
        models: Selected model aliases.

    Returns:
        Tuple of available model tuples and skipped model diagnostics.
    """
    settings = get_settings()
    available: list[tuple[str, str]] = []
    skipped: list[dict[str, str]] = []

    for alias in models:
        model_spec = EVAL_MODEL_SPECS[alias]
        provider = model_spec.split(":", 1)[0]

        if provider == "openai" and not settings.openai_api_key:
            skipped.append({"alias": alias, "reason": "OPENAI_API_KEY not configured"})
            continue
        if provider == "anthropic" and not settings.anthropic_api_key:
            skipped.append({"alias": alias, "reason": "ANTHROPIC_API_KEY not configured"})
            continue
        if provider in {"google", "google-gla"} and not settings.google_api_key:
            skipped.append({"alias": alias, "reason": "GOOGLE_API_KEY not configured"})
            continue
        if provider == "cerebras" and not settings.cerebras_api_key:
            skipped.append({"alias": alias, "reason": "CEREBRAS_API_KEY not configured"})
            continue

        available.append((alias, model_spec))

    return available, skipped


def build_prompt_definitions(
    *,
    content_types: list[EvalContentType],
    longform_template: LongformTemplate,
    custom_longform_system_prompt: str | None,
    custom_longform_user_template: str | None,
    custom_longform_output_type: PromptType,
    custom_news_system_prompt: str | None,
    custom_news_user_template: str | None,
    custom_news_output_type: PromptType,
) -> list[dict[str, str]]:
    """Build prompt definitions so reports can show exact prompts used.

    Args:
        content_types: Selected content types.
        longform_template: Selected built-in long-form template.
        custom_longform_system_prompt: Optional custom long-form system prompt.
        custom_longform_user_template: Optional custom long-form user template.
        custom_longform_output_type: Output schema for custom long-form prompt.
        custom_news_system_prompt: Optional custom news system prompt.
        custom_news_user_template: Optional custom news user template.
        custom_news_output_type: Output schema for custom news prompt.

    Returns:
        Prompt definition rows for report rendering.
    """
    definitions: list[dict[str, str]] = []
    for content_type in content_types:
        system_prompt, user_template, prompt_type = resolve_prompt_for_source(
            content_type=content_type,
            longform_template=longform_template,
            custom_longform_system_prompt=custom_longform_system_prompt,
            custom_longform_user_template=custom_longform_user_template,
            custom_longform_output_type=custom_longform_output_type,
            custom_news_system_prompt=custom_news_system_prompt,
            custom_news_user_template=custom_news_user_template,
            custom_news_output_type=custom_news_output_type,
        )

        is_custom = (content_type == "news" and custom_news_system_prompt) or (
            content_type != "news" and custom_longform_system_prompt
        )
        definitions.append(
            {
                "content_type": content_type,
                "prompt_source": "custom" if is_custom else "builtin",
                "prompt_type": prompt_type,
                "system_prompt": system_prompt,
                "user_template": user_template,
            }
        )
    return definitions


def select_sources(
    *,
    content_ids: list[int],
    content_types: list[EvalContentType],
    recent_pool_size: int,
    sample_size: int,
    seed: int | None,
) -> tuple[list[Any], list[int]]:
    """Select eval source rows from explicit IDs or random sampling.

    Args:
        content_ids: Optional explicit content IDs.
        content_types: Content types for sampling.
        recent_pool_size: Recent window size for sampling.
        sample_size: Sample size for sampling mode.
        seed: Optional random seed.

    Returns:
        Tuple of selected source payloads and missing IDs.
    """
    with get_db() as db:
        if content_ids:
            rows = db.query(Content).filter(Content.id.in_(content_ids)).all()  # type: ignore[arg-type]
            row_by_id = {row.id: row for row in rows}

            sources: list[Any] = []
            missing_ids: list[int] = []
            for content_id in content_ids:
                row = row_by_id.get(content_id)
                if row is None:
                    missing_ids.append(content_id)
                    continue
                payload = build_eval_source_payload(row)
                if payload is None:
                    logger.warning(
                        "Skipping content_id=%s because no valid input text was found",
                        content_id,
                    )
                    continue
                sources.append(payload)
            return sources, missing_ids

        sample_map = select_eval_samples(
            db,
            content_types=content_types,
            recent_pool_size=recent_pool_size,
            sample_size=sample_size,
            seed=seed,
        )

    selected_sources: list[Any] = []
    for content_type in content_types:
        selected_sources.extend(sample_map.get(content_type, []))
    return selected_sources, []


def get_agent_for_prompt_type(model_spec: str, prompt_type: PromptType, system_prompt: str) -> Any:
    """Build an agent matching the requested output schema type.

    Args:
        model_spec: Full model spec.
        prompt_type: Prompt/output type.
        system_prompt: System prompt text.

    Returns:
        Configured pydantic-ai agent.
    """
    if prompt_type == "editorial_narrative":
        return get_basic_agent(model_spec, EditorialNarrativeSummary, system_prompt)
    output_type = resolve_summarization_output_type(prompt_type)
    return get_basic_agent(model_spec, output_type, system_prompt)


def run_single_model_call(
    *,
    source: Any,
    model_alias: str,
    model_spec: str,
    timeout_seconds: int,
    max_retries: int,
    retry_backoff_seconds: float,
    max_input_chars: int,
    longform_template: LongformTemplate,
    custom_longform_system_prompt: str | None,
    custom_longform_user_template: str | None,
    custom_longform_output_type: PromptType,
    custom_news_system_prompt: str | None,
    custom_news_user_template: str | None,
    custom_news_output_type: PromptType,
) -> dict[str, Any]:
    """Run one model against one content item with retry support.

    Args:
        source: Source payload from ``build_eval_source_payload``.
        model_alias: Model alias key.
        model_spec: Full model spec string.
        timeout_seconds: Per-call timeout.
        max_retries: Number of retries after the first attempt.
        retry_backoff_seconds: Base backoff multiplier between retries.
        max_input_chars: Maximum input size after clipping.
        longform_template: Built-in long-form template key.
        custom_longform_system_prompt: Optional custom long-form system prompt.
        custom_longform_user_template: Optional custom long-form user template.
        custom_longform_output_type: Output schema for custom long-form prompts.
        custom_news_system_prompt: Optional custom news system prompt.
        custom_news_user_template: Optional custom news user template.
        custom_news_output_type: Output schema for custom news prompts.

    Returns:
        Model cell result for JSON + HTML rendering.
    """
    system_prompt, user_template, prompt_type = resolve_prompt_for_source(
        content_type=source.content_type,
        longform_template=longform_template,
        custom_longform_system_prompt=custom_longform_system_prompt,
        custom_longform_user_template=custom_longform_user_template,
        custom_longform_output_type=custom_longform_output_type,
        custom_news_system_prompt=custom_news_system_prompt,
        custom_news_user_template=custom_news_user_template,
        custom_news_output_type=custom_news_output_type,
    )
    if "{content}" not in user_template:
        raise ValueError("User template must include a {content} placeholder")

    clipped_input = clip_eval_input(source.input_text, max_input_chars)
    title_prefix = f"Title: {source.source_title}\n\n" if source.source_title else ""
    user_message = user_template.format(content=f"{title_prefix}{clipped_input}")

    request_chars = len(system_prompt) + len(user_message)
    request_tokens_estimate = estimate_tokens_from_chars(request_chars)
    attempts = max_retries + 1

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        try:
            agent = get_agent_for_prompt_type(model_spec, prompt_type, system_prompt)
            result = agent.run_sync(
                user_message,
                model_settings={"timeout": timeout_seconds},
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            usage = extract_usage(result)
            payload = extract_result_payload(result)
            output_chars = len(json.dumps(payload, ensure_ascii=False))

            logger.info(
                "Eval success content_id=%s model=%s attempt=%s latency_ms=%s req_chars=%s",
                source.content_id,
                model_alias,
                attempt,
                latency_ms,
                request_chars,
            )
            return {
                "model_alias": model_alias,
                "model_label": EVAL_MODEL_LABELS.get(model_alias, model_alias),
                "model_spec": model_spec,
                "status": "ok",
                "attempt": attempt,
                "prompt_type": prompt_type,
                "latency_ms": latency_ms,
                "usage": usage,
                "request_chars": request_chars,
                "request_tokens_estimate": request_tokens_estimate,
                "output_chars": output_chars,
                "output": payload,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - started) * 1000)
            last_error = exc
            logger.error(
                "Eval failure content_id=%s model=%s attempt=%s/%s latency_ms=%s req_chars=%s error=%s",
                source.content_id,
                model_alias,
                attempt,
                attempts,
                latency_ms,
                request_chars,
                str(exc),
            )
            if attempt < attempts:
                time.sleep(retry_backoff_seconds * attempt)

    assert last_error is not None
    return {
        "model_alias": model_alias,
        "model_label": EVAL_MODEL_LABELS.get(model_alias, model_alias),
        "model_spec": model_spec,
        "status": "error",
        "attempt": attempts,
        "prompt_type": prompt_type,
        "latency_ms": None,
        "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None},
        "request_chars": request_chars,
        "request_tokens_estimate": request_tokens_estimate,
        "output_chars": 0,
        "output": None,
        "error": str(last_error),
    }


def build_aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build aggregate metrics across all model cells.

    Args:
        results: Item-level results list.

    Returns:
        Aggregate metrics dictionary.
    """
    cells = [cell for item in results for cell in item.get("model_results", [])]
    successful = [cell for cell in cells if cell.get("status") == "ok"]

    def average(values: list[int]) -> float | None:
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    latency_values = [
        int(cell["latency_ms"]) for cell in successful if cell.get("latency_ms") is not None
    ]
    in_token_values = [
        int(cell["usage"]["input_tokens"])
        for cell in successful
        if cell.get("usage", {}).get("input_tokens") is not None
    ]
    out_token_values = [
        int(cell["usage"]["output_tokens"])
        for cell in successful
        if cell.get("usage", {}).get("output_tokens") is not None
    ]

    return {
        "items_total": len(results),
        "cells_total": len(cells),
        "cells_successful": len(successful),
        "cells_failed": len(cells) - len(successful),
        "avg_latency_ms": average(latency_values),
        "avg_input_tokens": average(in_token_values),
        "avg_output_tokens": average(out_token_values),
    }


def _get_text(value: Any, *, keys: tuple[str, ...] = ("text",)) -> str:
    """Extract a text value from a string or dict."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return ""


def _collect_text_items(values: Any, *, keys: tuple[str, ...] = ("text",)) -> list[str]:
    """Collect textual list items from mixed list payloads."""
    if not isinstance(values, list):
        return []
    items: list[str] = []
    for entry in values:
        text = _get_text(entry, keys=keys)
        if text:
            items.append(text)
    return items


def _render_paragraphs(text: str) -> str:
    """Render multi-paragraph plain text as HTML."""
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    if not paragraphs:
        return ""
    return "".join(f"<p>{html_escape(paragraph)}</p>" for paragraph in paragraphs)


def _render_string_list(items: list[str], class_name: str = "output-list") -> str:
    """Render a plain string list as HTML."""
    if not items:
        return ""
    rows = "".join(f"<li>{html_escape(item)}</li>" for item in items)
    return f'<ul class="{class_name}">{rows}</ul>'


def _render_quotes(quotes: Any) -> str:
    """Render quote rows with optional attribution/context."""
    if not isinstance(quotes, list) or not quotes:
        return ""

    rows: list[str] = []
    for quote in quotes:
        text = _get_text(quote, keys=("text", "quote"))
        if not text:
            continue
        attribution = ""
        context = ""
        if isinstance(quote, dict):
            attribution = _get_text(quote, keys=("attribution",))
            context = _get_text(quote, keys=("context",))
        meta_parts = [part for part in [attribution, context] if part]
        meta_html = (
            f'<div class="quote-meta">{" · ".join(html_escape(part) for part in meta_parts)}</div>'
            if meta_parts
            else ""
        )
        rows.append(
            f"""
            <li class="quote-item">
              <blockquote>{html_escape(text)}</blockquote>
              {meta_html}
            </li>
            """
        )

    if not rows:
        return ""
    return f'<ul class="quote-list">{"".join(rows)}</ul>'


def _render_topics(topics: Any) -> str:
    """Render interleaved topic blocks."""
    if not isinstance(topics, list) or not topics:
        return ""
    topic_rows: list[str] = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        topic_name = _get_text(topic, keys=("topic", "title"))
        bullets = _collect_text_items(topic.get("bullets"), keys=("text", "point"))
        if not topic_name and not bullets:
            continue
        bullets_html = _render_string_list(bullets, class_name="topic-bullets")
        topic_rows.append(
            f"""
            <li class="topic-item">
              <h6>{html_escape(topic_name or "Topic")}</h6>
              {bullets_html}
            </li>
            """
        )
    if not topic_rows:
        return ""
    return f'<ul class="topic-list">{"".join(topic_rows)}</ul>'


def _render_bulleted_points(points: Any) -> str:
    """Render long-bullet summary points."""
    if not isinstance(points, list) or not points:
        return ""

    rows: list[str] = []
    for point in points:
        point_text = _get_text(point, keys=("text", "point"))
        detail = _get_text(point, keys=("detail", "insight"))
        point_quotes = ""
        if isinstance(point, dict):
            point_quotes = _render_quotes(point.get("quotes"))
        if not point_text and not detail and not point_quotes:
            continue

        rows.append(
            f"""
            <li class="bullet-point-item">
              <div class="point-text">{html_escape(point_text)}</div>
              <div class="point-detail">{_render_paragraphs(detail) if detail else ""}</div>
              {point_quotes}
            </li>
            """
        )
    if not rows:
        return ""
    return f'<ol class="bullet-point-list">{"".join(rows)}</ol>'


def _render_output_payload(payload: dict[str, Any]) -> str:
    """Render known summary payload shapes into readable HTML."""
    blocks: list[str] = []

    classification = _get_text(payload, keys=("classification",))
    if classification:
        blocks.append(f'<div class="class-pill">{html_escape(classification)}</div>')

    title = _get_text(payload, keys=("title",))
    if title:
        blocks.append(f"<h5>{html_escape(title)}</h5>")

    if "editorial_narrative" in payload:
        narrative = _get_text(payload, keys=("editorial_narrative",))
        if narrative:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Narrative</h6>
                  {_render_paragraphs(narrative)}
                </section>
                """
            )
        key_points = _collect_text_items(payload.get("key_points"), keys=("point", "text"))
        if key_points:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Key Points</h6>
                  {_render_string_list(key_points)}
                </section>
                """
            )
        quotes_html = _render_quotes(payload.get("quotes"))
        if quotes_html:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Quotes</h6>
                  {quotes_html}
                </section>
                """
            )
        return "".join(blocks)

    if isinstance(payload.get("points"), list):
        points_html = _render_bulleted_points(payload.get("points"))
        if points_html:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Detailed Points</h6>
                  {points_html}
                </section>
                """
            )
        return "".join(blocks)

    if isinstance(payload.get("key_points"), list) and isinstance(payload.get("topics"), list):
        hook = _get_text(payload, keys=("hook",))
        if hook:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Hook</h6>
                  {_render_paragraphs(hook)}
                </section>
                """
            )
        key_points = _collect_text_items(payload.get("key_points"), keys=("text", "point"))
        if key_points:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Key Points</h6>
                  {_render_string_list(key_points)}
                </section>
                """
            )
        topics_html = _render_topics(payload.get("topics"))
        if topics_html:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Topics</h6>
                  {topics_html}
                </section>
                """
            )
        quotes_html = _render_quotes(payload.get("quotes"))
        if quotes_html:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Quotes</h6>
                  {quotes_html}
                </section>
                """
            )
        takeaway = _get_text(payload, keys=("takeaway",))
        if takeaway:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Takeaway</h6>
                  {_render_paragraphs(takeaway)}
                </section>
                """
            )
        return "".join(blocks)

    if "bullet_points" in payload or "overview" in payload:
        overview = _get_text(payload, keys=("overview", "summary"))
        if overview:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Overview</h6>
                  {_render_paragraphs(overview)}
                </section>
                """
            )
        bullet_points = _collect_text_items(payload.get("bullet_points"), keys=("text", "point"))
        if bullet_points:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Bullet Points</h6>
                  {_render_string_list(bullet_points)}
                </section>
                """
            )
        quotes_html = _render_quotes(payload.get("quotes"))
        if quotes_html:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Quotes</h6>
                  {quotes_html}
                </section>
                """
            )
        questions = _collect_text_items(payload.get("questions"), keys=("text",))
        if questions:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Questions</h6>
                  {_render_string_list(questions)}
                </section>
                """
            )
        counter_arguments = _collect_text_items(
            payload.get("counter_arguments"),
            keys=("text", "point"),
        )
        if counter_arguments:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Counter-Arguments</h6>
                  {_render_string_list(counter_arguments)}
                </section>
                """
            )
        return "".join(blocks)

    summary_text = _get_text(payload, keys=("summary", "overview", "takeaway"))
    if summary_text:
        blocks.append(
            f"""
            <section class="output-section">
              <h6>Summary</h6>
              {_render_paragraphs(summary_text)}
            </section>
            """
        )
    key_points = _collect_text_items(payload.get("key_points"), keys=("text", "point"))
    if key_points:
        blocks.append(
            f"""
            <section class="output-section">
              <h6>Key Points</h6>
              {_render_string_list(key_points)}
            </section>
            """
        )

    if blocks:
        return "".join(blocks)
    return '<p class="empty-output">No structured fields found. Open Raw JSON below.</p>'


def render_html(report_payload: dict[str, Any]) -> str:
    """Render a complete static HTML report.

    Args:
        report_payload: Report data payload.

    Returns:
        Full HTML document text.
    """
    config = report_payload["config"]
    aggregate = report_payload["aggregate"]
    models = report_payload["available_models"]
    skipped_models = report_payload["skipped_models"]
    prompt_definitions = report_payload.get("prompt_definitions", [])
    results = report_payload["results"]

    model_names = ", ".join(f"{m['label']} ({m['alias']})" for m in models) or "None"
    skipped_text = (
        " | ".join(f"{item['alias']}: {item['reason']}" for item in skipped_models) if skipped_models else "None"
    )
    prompt_cards = "".join(
        f"""
        <article class="prompt-card">
          <h3>{html_escape(str(prompt.get("content_type", "unknown")))} · {html_escape(str(prompt.get("prompt_type", "unknown")))}</h3>
          <p class="detail"><strong>Source:</strong> {html_escape(str(prompt.get("prompt_source", "unknown")))}</p>
          <details open>
            <summary>System Prompt</summary>
            <pre>{html_escape(str(prompt.get("system_prompt", "")))}</pre>
          </details>
          <details>
            <summary>User Template</summary>
            <pre>{html_escape(str(prompt.get("user_template", "")))}</pre>
          </details>
        </article>
        """
        for prompt in prompt_definitions
    )

    sections: list[str] = []
    for item in results:
        ok_cells = [cell for cell in item["model_results"] if cell.get("status") == "ok"]
        error_cells = [cell for cell in item["model_results"] if cell.get("status") != "ok"]

        model_columns: list[str] = []
        for cell in ok_cells:
            usage = cell["usage"] or {}
            output = cell["output"] if isinstance(cell.get("output"), dict) else {}
            payload_text = json.dumps(cell["output"], ensure_ascii=False, indent=2)
            model_columns.append(
                f"""
                <article class="model-card ok">
                  <header>
                    <h4>{html_escape(cell["model_label"])}</h4>
                    <p class="mono">{html_escape(cell["model_spec"])}</p>
                    <p class="status ok">{html_escape(cell["status"])}</p>
                  </header>
                  <dl class="metrics">
                    <div><dt>Attempt</dt><dd>{cell["attempt"]}</dd></div>
                    <div><dt>Latency</dt><dd>{cell["latency_ms"] if cell["latency_ms"] is not None else "n/a"} ms</dd></div>
                    <div><dt>Input Tokens</dt><dd>{usage.get("input_tokens") if usage.get("input_tokens") is not None else "n/a"}</dd></div>
                    <div><dt>Output Tokens</dt><dd>{usage.get("output_tokens") if usage.get("output_tokens") is not None else "n/a"}</dd></div>
                    <div><dt>Total Tokens</dt><dd>{usage.get("total_tokens") if usage.get("total_tokens") is not None else "n/a"}</dd></div>
                    <div><dt>Request Chars</dt><dd>{cell["request_chars"]}</dd></div>
                    <div><dt>Req Tokens (est)</dt><dd>{cell["request_tokens_estimate"]}</dd></div>
                    <div><dt>Output Chars</dt><dd>{cell["output_chars"]}</dd></div>
                  </dl>
                  <section class="output-body">
                    {_render_output_payload(output)}
                  </section>
                  <details>
                    <summary>Raw JSON</summary>
                    <pre>{html_escape(payload_text)}</pre>
                  </details>
                </article>
                """
            )

        failed_rows = "".join(
            f"""
            <li>
              <span class="mono">{html_escape(cell["model_label"])}</span>
              <span class="failure-error">{html_escape(cell.get("error") or "Unknown error")}</span>
            </li>
            """
            for cell in error_cells
        )
        failures_block = (
            f"""
            <details class="failure-list">
              <summary>Failed Providers ({len(error_cells)})</summary>
              <ul>{failed_rows}</ul>
            </details>
            """
            if error_cells
            else ""
        )

        model_grid = (
            f'<div class="model-grid">{"".join(model_columns)}</div>'
            if model_columns
            else '<p class="all-failed">No successful model responses for this content item.</p>'
        )
        sections.append(
            f"""
            <section class="content-card">
              <header class="content-header">
                <div class="meta">
                  <span class="pill">{html_escape(item["content_type"])}</span>
                  <span>ID {item["content_id"]}</span>
                  <span>Input chars {item["input_chars"]}</span>
                </div>
                <h3>{html_escape(item["source_title"] or "Untitled")}</h3>
                <a href="{html_escape(item["url"])}" target="_blank">{html_escape(item["url"])}</a>
              </header>
              {model_grid}
              {failures_block}
            </section>
            """
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>LLM Eval Report</title>
  <style>
    :root {{
      --bg: #f6f8fb;
      --card: #ffffff;
      --text: #101828;
      --muted: #475467;
      --ok: #027a48;
      --ok-bg: #ecfdf3;
      --error: #b42318;
      --error-bg: #fef3f2;
      --border: #d0d5dd;
      --mono-bg: #101828;
      --mono-fg: #e4e7ec;
      --accent: #175cd3;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: linear-gradient(180deg, #ffffff 0%, var(--bg) 220px);
    }}
    .container {{ max-width: 1600px; margin: 0 auto; padding: 24px; }}
    .summary {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 18px;
      margin-bottom: 18px;
      box-shadow: 0 8px 30px rgba(16, 24, 40, 0.06);
    }}
    h1 {{ margin: 0 0 10px 0; font-size: 28px; }}
    h2 {{ margin: 0 0 8px 0; font-size: 18px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px;
      margin-top: 12px;
    }}
    .prompt-section {{
      margin-top: 14px;
      border-top: 1px solid var(--border);
      padding-top: 12px;
    }}
    .prompt-wrap {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 10px;
      margin-top: 8px;
    }}
    .prompt-card {{
      border: 1px solid var(--border);
      border-radius: 10px;
      background: #f8fafc;
      padding: 10px;
    }}
    .prompt-card h3 {{
      margin: 0;
      font-size: 14px;
    }}
    .stat {{
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 10px;
      background: #f8fafc;
    }}
    .stat .label {{ color: var(--muted); font-size: 12px; }}
    .stat .value {{ font-size: 20px; font-weight: 700; }}
    .detail {{ margin-top: 8px; color: var(--muted); font-size: 13px; }}
    .content-card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 16px;
      margin-bottom: 14px;
      box-shadow: 0 8px 30px rgba(16, 24, 40, 0.05);
    }}
    .content-header h3 {{ margin: 8px 0; font-size: 20px; }}
    .content-header a {{ color: var(--accent); text-decoration: none; word-break: break-all; }}
    .meta {{ display: flex; gap: 8px; flex-wrap: wrap; color: var(--muted); font-size: 12px; }}
    .pill {{
      border: 1px solid #84adff;
      color: #1849a9;
      border-radius: 999px;
      padding: 2px 8px;
      font-weight: 600;
      background: #eff8ff;
    }}
    .model-grid {{
      margin-top: 14px;
      display: flex;
      flex-wrap: nowrap;
      overflow-x: auto;
      gap: 12px;
      padding-bottom: 6px;
      scroll-snap-type: x proximity;
    }}
    .model-card {{
      flex: 0 0 min(460px, calc(100vw - 64px));
      scroll-snap-align: start;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px;
      background: #fff;
      max-height: 82vh;
      overflow-y: auto;
    }}
    .model-card.ok {{ background: var(--ok-bg); border-color: #6ce9a6; }}
    .model-card.error {{ background: var(--error-bg); border-color: #fda29b; }}
    .model-card h4 {{ margin: 0; font-size: 16px; }}
    .mono {{ font-family: ui-monospace, Menlo, Monaco, "Cascadia Mono", monospace; font-size: 12px; color: var(--muted); margin: 4px 0 0; }}
    .status {{
      display: inline-block;
      margin-top: 6px;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .status.ok {{ color: var(--ok); background: #dcfae6; }}
    .status.error {{ color: var(--error); background: #fee4e2; }}
    .metrics {{
      margin: 10px 0;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px 10px;
    }}
    .metrics dt {{ font-size: 12px; color: var(--muted); }}
    .metrics dd {{ margin: 0; font-size: 13px; font-weight: 600; }}
    .output-body {{
      margin-top: 12px;
      border-top: 1px solid rgba(16, 24, 40, 0.1);
      padding-top: 10px;
      display: grid;
      gap: 10px;
    }}
    .output-body h5 {{
      margin: 0;
      font-size: 18px;
      line-height: 1.25;
    }}
    .output-body h6 {{
      margin: 0 0 6px;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
    }}
    .output-body p {{
      margin: 0;
      font-size: 14px;
      line-height: 1.45;
    }}
    .output-section {{
      display: grid;
      gap: 6px;
    }}
    .class-pill {{
      display: inline-flex;
      width: fit-content;
      border: 1px solid #84adff;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      color: #1849a9;
      background: #eff8ff;
    }}
    .output-list, .topic-bullets {{
      margin: 0;
      padding-left: 18px;
      display: grid;
      gap: 6px;
    }}
    .bullet-point-list {{
      margin: 0;
      padding-left: 20px;
      display: grid;
      gap: 8px;
    }}
    .bullet-point-item {{
      display: grid;
      gap: 6px;
    }}
    .point-text {{
      font-weight: 700;
      line-height: 1.4;
    }}
    .point-detail {{
      color: #344054;
    }}
    .quote-list {{
      margin: 0;
      padding-left: 0;
      list-style: none;
      display: grid;
      gap: 8px;
    }}
    .quote-item {{
      border-left: 3px solid #84adff;
      padding-left: 10px;
    }}
    .quote-item blockquote {{
      margin: 0;
      font-style: italic;
      color: #344054;
    }}
    .quote-meta {{
      margin-top: 4px;
      font-size: 12px;
      color: var(--muted);
    }}
    .topic-list {{
      margin: 0;
      padding-left: 0;
      list-style: none;
      display: grid;
      gap: 8px;
    }}
    .topic-item {{
      border: 1px solid rgba(23, 92, 211, 0.25);
      border-radius: 8px;
      padding: 8px;
      background: rgba(255, 255, 255, 0.55);
    }}
    .topic-item h6 {{
      margin: 0 0 6px;
      font-size: 13px;
      color: #1849a9;
      text-transform: none;
      letter-spacing: 0;
    }}
    .empty-output {{
      margin: 0;
      color: var(--muted);
    }}
    .all-failed {{
      margin: 12px 0 0;
      color: var(--error);
      font-weight: 600;
    }}
    .failure-list {{
      margin-top: 12px;
      border-top: 1px dashed #fda29b;
      padding-top: 10px;
    }}
    .failure-list ul {{
      margin: 10px 0 0;
      padding-left: 18px;
      display: grid;
      gap: 8px;
    }}
    .failure-error {{
      color: var(--error);
      font-size: 13px;
      margin-left: 8px;
    }}
    details {{ margin-top: 10px; }}
    summary {{ cursor: pointer; font-size: 13px; color: var(--accent); font-weight: 600; }}
    pre {{
      margin: 10px 0 0;
      padding: 10px;
      border-radius: 8px;
      background: var(--mono-bg);
      color: var(--mono-fg);
      font-size: 12px;
      line-height: 1.45;
      overflow: auto;
      max-height: 460px;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    @media (max-width: 900px) {{
      .container {{ padding: 14px; }}
      .model-card {{ flex-basis: calc(100vw - 40px); max-height: none; }}
    }}
  </style>
</head>
<body>
  <main class="container">
    <section class="summary">
      <h1>LLM Eval Report</h1>
      <p class="detail"><strong>Generated:</strong> {html_escape(report_payload["run_completed_at"])}</p>
      <p class="detail"><strong>Models:</strong> {html_escape(model_names)}</p>
      <p class="detail"><strong>Skipped Models:</strong> {html_escape(skipped_text)}</p>
      <p class="detail"><strong>Config:</strong> content_types={html_escape(','.join(config["content_types"]))}, sample_size={config["sample_size"]}, recent_pool_size={config["recent_pool_size"]}, longform_template={html_escape(config["longform_template"])}, seed={html_escape(str(config["seed"]))}</p>
      <div class="grid">
        <div class="stat"><div class="label">Items</div><div class="value">{aggregate["items_total"]}</div></div>
        <div class="stat"><div class="label">Cells Total</div><div class="value">{aggregate["cells_total"]}</div></div>
        <div class="stat"><div class="label">Cells Success</div><div class="value">{aggregate["cells_successful"]}</div></div>
        <div class="stat"><div class="label">Cells Failed</div><div class="value">{aggregate["cells_failed"]}</div></div>
        <div class="stat"><div class="label">Avg Latency (ms)</div><div class="value">{aggregate["avg_latency_ms"] if aggregate["avg_latency_ms"] is not None else "n/a"}</div></div>
        <div class="stat"><div class="label">Avg In Tokens</div><div class="value">{aggregate["avg_input_tokens"] if aggregate["avg_input_tokens"] is not None else "n/a"}</div></div>
        <div class="stat"><div class="label">Avg Out Tokens</div><div class="value">{aggregate["avg_output_tokens"] if aggregate["avg_output_tokens"] is not None else "n/a"}</div></div>
      </div>
      <section class="prompt-section">
        <h2>Prompts Used</h2>
        <div class="prompt-wrap">
          {prompt_cards}
        </div>
      </section>
    </section>
    {''.join(sections)}
  </main>
</body>
</html>"""


def html_escape(value: str) -> str:
    """Escape HTML special characters.

    Args:
        value: Raw string value.

    Returns:
        HTML-escaped string.
    """
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def main() -> int:
    """Run report generation end-to-end.

    Returns:
        Process exit code.
    """
    args = parse_args()
    setup_logging(name="eval_html_report", level="INFO")
    init_db()

    try:
        content_types = validate_content_types(parse_csv_list(args.content_types))
        models = validate_models(parse_csv_list(args.models))
        content_ids = parse_content_ids(args.content_ids)
        ensure_prompt_override_pair(
            args.custom_longform_system_prompt_file,
            args.custom_longform_user_template_file,
            "Longform",
        )
        ensure_prompt_override_pair(
            args.custom_news_system_prompt_file,
            args.custom_news_user_template_file,
            "News",
        )
    except ValueError as exc:
        logger.error("Invalid arguments: %s", str(exc))
        return 2

    custom_longform_system_prompt = (
        load_prompt_file(args.custom_longform_system_prompt_file)
        if args.custom_longform_system_prompt_file
        else None
    )
    custom_longform_user_template = (
        load_prompt_file(args.custom_longform_user_template_file)
        if args.custom_longform_user_template_file
        else None
    )
    custom_news_system_prompt = (
        load_prompt_file(args.custom_news_system_prompt_file)
        if args.custom_news_system_prompt_file
        else None
    )
    custom_news_user_template = (
        load_prompt_file(args.custom_news_user_template_file)
        if args.custom_news_user_template_file
        else None
    )
    effective_parallel_model_calls = 1
    if args.parallel_model_calls != 1:
        logger.warning(
            "Ignoring --parallel-model-calls=%s. Parallel execution is disabled to avoid event-loop/client errors.",
            args.parallel_model_calls,
        )

    output_dir = resolve_output_directory(args.output_dir)
    run_started_at = datetime.now(UTC)

    available_models, skipped_models = resolve_available_models(models)
    prompt_definitions = build_prompt_definitions(
        content_types=content_types,
        longform_template=args.longform_template,
        custom_longform_system_prompt=custom_longform_system_prompt,
        custom_longform_user_template=custom_longform_user_template,
        custom_longform_output_type=args.custom_longform_output_type,
        custom_news_system_prompt=custom_news_system_prompt,
        custom_news_user_template=custom_news_user_template,
        custom_news_output_type=args.custom_news_output_type,
    )
    selected_sources, missing_ids = select_sources(
        content_ids=content_ids,
        content_types=content_types,
        recent_pool_size=args.recent_pool_size,
        sample_size=args.sample_size,
        seed=args.seed,
    )
    if missing_ids:
        logger.warning("Missing content IDs: %s", ",".join(str(item) for item in missing_ids))

    logger.info(
        "Generating report for items=%s models=%s timeout=%ss retries=%s parallel=%s",
        len(selected_sources),
        len(available_models),
        args.timeout_seconds,
        args.max_retries,
        effective_parallel_model_calls,
    )

    item_results: list[dict[str, Any]] = []
    ordered_aliases = [alias for alias in models if any(alias == a for a, _ in available_models)]
    model_spec_map = dict(available_models)

    for source in selected_sources:
        model_results_by_alias: dict[str, dict[str, Any]] = {}
        model_pairs = [(alias, model_spec_map[alias]) for alias in ordered_aliases]
        for alias, model_spec in model_pairs:
            model_results_by_alias[alias] = run_single_model_call(
                source=source,
                model_alias=alias,
                model_spec=model_spec,
                timeout_seconds=args.timeout_seconds,
                max_retries=args.max_retries,
                retry_backoff_seconds=args.retry_backoff_seconds,
                max_input_chars=args.max_input_chars,
                longform_template=args.longform_template,
                custom_longform_system_prompt=custom_longform_system_prompt,
                custom_longform_user_template=custom_longform_user_template,
                custom_longform_output_type=args.custom_longform_output_type,
                custom_news_system_prompt=custom_news_system_prompt,
                custom_news_user_template=custom_news_user_template,
                custom_news_output_type=args.custom_news_output_type,
            )

        item_results.append(
            {
                "content_id": source.content_id,
                "content_type": source.content_type,
                "created_at": source.created_at,
                "url": source.url,
                "source_title": source.source_title,
                "existing_summary_title": source.existing_summary_title,
                "input_chars": source.input_chars,
                "model_results": [model_results_by_alias[alias] for alias in ordered_aliases],
            }
        )

    report_payload = {
        "run_started_at": run_started_at.isoformat(),
        "run_completed_at": datetime.now(UTC).isoformat(),
        "config": {
            "content_types": content_types,
            "models": models,
            "longform_template": args.longform_template,
            "recent_pool_size": args.recent_pool_size,
            "sample_size": args.sample_size,
            "seed": args.seed,
            "content_ids": content_ids,
            "timeout_seconds": args.timeout_seconds,
            "max_retries": args.max_retries,
            "parallel_model_calls_requested": args.parallel_model_calls,
            "parallel_model_calls_effective": effective_parallel_model_calls,
            "max_input_chars": args.max_input_chars,
            "custom_longform_prompt_enabled": bool(custom_longform_system_prompt),
            "custom_news_prompt_enabled": bool(custom_news_system_prompt),
        },
        "available_models": [
            {"alias": alias, "label": EVAL_MODEL_LABELS.get(alias, alias), "model_spec": model_spec}
            for alias, model_spec in available_models
        ],
        "skipped_models": skipped_models,
        "prompt_definitions": prompt_definitions,
        "missing_ids": missing_ids,
        "results": item_results,
        "aggregate": build_aggregate(item_results),
    }

    results_json_path = output_dir / "results.json"
    index_html_path = output_dir / "index.html"

    results_json_path.write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    index_html_path.write_text(render_html(report_payload), encoding="utf-8")

    logger.info("Report written: %s", str(index_html_path))
    logger.info("Raw JSON written: %s", str(results_json_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
