#!/usr/bin/env python3
"""Generate a static HTML eval report with side-by-side model outputs."""

from __future__ import annotations

# ruff: noqa: E501
import argparse
import json
import math
import os
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, NamedTuple, cast

from sqlalchemy import desc

# Add project root to import path when running as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import get_db, init_db
from app.core.logging import get_logger, setup_logging
from app.core.settings import get_settings
from app.models.contracts import NewsItemStatus
from app.models.db import Content, NewsItem
from app.services.admin_eval import (
    EVAL_MODEL_LABELS,
    EVAL_MODEL_SPECS,
    MAX_EVAL_INPUT_CHARS,
    EvalSourcePayload,
    build_eval_source_payload,
    select_eval_samples,
)
from app.services.llm_agents import get_basic_agent
from app.services.llm_prompts import generate_summary_prompt
from app.services.llm_summarization import resolve_summarization_output_type
from app.services.news_article_bodies import get_news_item_article_body_resolver
from app.services.news_processing import _build_processing_prompt
from app.services.summarization_templates import resolve_summarization_prompt_route

logger = get_logger(__name__)

EvalContentType = Literal["article", "podcast", "news"]
LongformTemplate = Literal[
    "source_aware_editorial_v2",
    "long_bullets_v1",
    "interleaved_v2",
    "structured_v1",
    "editorial_narrative_v1",
]
PromptType = Literal[
    "long_bullets",
    "interleaved",
    "structured",
    "news",
    "editorial_narrative",
    "editorial_podcast",
    "editorial_substack",
    "editorial_twitter",
    "editorial_research",
    "editorial_github",
    "longform_artifact",
]

ESTIMATED_CHARS_PER_TOKEN = 4
OPENROUTER_REASONING_OFF_ALIAS = "openrouter_deepseek_flash_reasoning_off"
OPENROUTER_REASONING_ON_ALIAS = "openrouter_deepseek_flash_reasoning_on"
REPORT_MODEL_SPECS = {
    **EVAL_MODEL_SPECS,
    OPENROUTER_REASONING_OFF_ALIAS: EVAL_MODEL_SPECS["openrouter_deepseek_flash"],
    OPENROUTER_REASONING_ON_ALIAS: EVAL_MODEL_SPECS["openrouter_deepseek_flash"],
}
REPORT_MODEL_LABELS = {
    **EVAL_MODEL_LABELS,
    OPENROUTER_REASONING_OFF_ALIAS: "OpenRouter DeepSeek V4 Flash (Reasoning Off)",
    OPENROUTER_REASONING_ON_ALIAS: "OpenRouter DeepSeek V4 Flash (Reasoning On)",
}
REPORT_MODEL_SETTINGS_BY_ALIAS: dict[str, dict[str, Any]] = {
    OPENROUTER_REASONING_OFF_ALIAS: {
        "openrouter_reasoning": {"enabled": False, "exclude": True},
    },
    OPENROUTER_REASONING_ON_ALIAS: {
        "openrouter_reasoning": {"enabled": True, "exclude": True},
    },
}
NEWS_STATUS_ORDER = tuple(status.value for status in NewsItemStatus)


class NewsPromptVariant(NamedTuple):
    alias: str
    label: str
    description: str
    system_prompt: str
    user_template: str
    output_type: PromptType


NEWS_PROMPT_VARIANT_ORDER = (
    "current",
    "reader_impact",
    "evidence_first",
    "feed_scan",
    "key_point_depth",
    "source_backed_four",
    "decision_brief",
    "fact_dense",
)
NEWS_PROMPT_VARIANT_USER_TEMPLATE = "Article & Aggregator Context:\n\n{content}"
CUSTOM_NEWS_PROMPT_VARIANTS: dict[str, tuple[str, str, str]] = {
    "reader_impact": (
        "Reader Impact",
        "Prioritizes why a busy technical reader should care.",
        """You are an expert news editor writing for a busy technical reader. Read the provided
article content and aggregator context, then produce a concise, readable summary matching the
provided structured output schema.

Field guidance:
- title: direct factual headline, <=95 characters; name the actor and concrete development.
- article_url: canonical article URL when available.
- key_points: include 2-4 complete, self-contained sentences, <=220 characters each.
- summary: required 2-3 sentence overview paragraph, 180-500 characters when the source supports it.
- classification: use "to_read" for concrete news, useful analysis, or practical signal; use "skip" for low-value or promotional content.

Rules:
- Lead with the most consequential thing that happened and why it matters.
- Prefer specific companies, products, numbers, dates, constraints, and affected users over generic category labels.
- Do not inflate weak evidence. If the source only states a claim, summarize it as a claim.
- Avoid clipped headline fragments, markdown, numbering, topics, quotes, or extra fields.
- If the item is a post rather than an article, summarize the post's substantive claim instead of inventing broader news.
""",
    ),
    "evidence_first": (
        "Evidence First",
        "Forces source-grounded facts and downranks aggregator-only framing.",
        """You are a careful news summarization editor. Read the article content and aggregator
context as evidence, then produce a structured news summary that stays tightly grounded in what
the evidence actually says.

Field guidance:
- title: factual headline, <=95 characters, based on the strongest source-backed fact.
- article_url: canonical article URL when available.
- key_points: include 2-4 source-grounded points, usually complete sentences, <=220 characters each.
- summary: required 2-3 sentence overview paragraph, 180-500 characters when enough evidence exists.
- classification: use "to_read" for substantial signal and "skip" when the evidence is thin, generic, promotional, or mostly metadata.

Rules:
- Prefer article body evidence over aggregator headlines; use aggregator context only when it adds source, author, discussion, or distribution signal.
- Preserve exact names, technical terms, numbers, and dates.
- Distinguish stated facts from speculation, reactions, or implications.
- Do not add background, market framing, or causal claims unless present in the evidence.
- Use natural prose. Never include markdown, topics, quotes, numbering, or fields outside the schema.
""",
    ),
    "feed_scan": (
        "Feed Scan",
        "Optimizes for fast mobile feed scanning without losing substance.",
        """You are the short-form news editor for a fast-scanning mobile feed. Read the provided
article content and aggregator context, then produce a compact but complete structured summary.

Field guidance:
- title: clear feed headline, <=95 characters, rewritten when the source title is vague or truncated.
- article_url: canonical article URL when available.
- key_points: include 2-4 scannable complete sentences, <=220 characters each.
- summary: required 2-3 sentence paragraph that reads naturally, usually 180-500 characters.
- classification: use "to_read" when the item gives the reader a concrete update; use "skip" for duplicate, promotional, or low-signal content.

Rules:
- Make the title and first key point useful even when read alone.
- Surface the practical consequence, product change, policy shift, funding move, benchmark, vulnerability, or disagreement when present.
- Avoid vague phrasing such as "raises questions", "sparks debate", or "could have implications" unless the evidence explains the specifics.
- Keep prose calm and factual, with no sensationalism.
- Never include markdown, topics, quotes, numbering, or extra fields.
""",
    ),
    "key_point_depth": (
        "Key Point Depth",
        "Pushes each key point to carry a distinct role and avoid thin repeats.",
        """You are a careful news editor optimizing short-form summaries for richer key points.
Read the article content and aggregator context as evidence, then produce a structured summary
matching the provided schema.

Field guidance:
- title: factual headline, <=95 characters, naming the actor and concrete development.
- article_url: canonical article URL when available.
- key_points: include 3-4 complete, source-grounded sentences, <=220 characters each; use 2 only when the evidence is genuinely too thin.
- summary: required 2-3 sentence overview paragraph, 180-500 characters when enough evidence exists.
- classification: use "to_read" for substantial signal and "skip" for thin, generic, duplicate, or promotional items.

Rules:
- Make each key point do different work: what changed, the evidence/details, why it matters, and any caveat or next step present in the source.
- Prefer exact names, products, numbers, dates, locations, technical terms, and quoted claims over vague categories.
- Do not repeat the title in the key points unless it adds a new fact.
- Distinguish facts from claims, reactions, and speculation.
- Never include markdown, numbering, topics, quotes, or fields outside the schema.
""",
    ),
    "source_backed_four": (
        "Source-Backed 4",
        "Prefers four source-backed points with no invented filler.",
        """You are a source-grounded news summarization editor. Use the article body first and
aggregator context second. Produce a structured news summary that is useful in a compact feed.

Field guidance:
- title: direct factual headline, <=95 characters.
- article_url: canonical article URL when available.
- key_points: prefer 4 source-backed key points, each a complete sentence <=220 characters; fall back to 3 or 2 only when evidence is limited.
- summary: required 2-3 sentence overview paragraph, 180-500 characters when the source supports it.
- classification: use "to_read" for concrete, useful signal and "skip" for low-signal items.

Rules:
- Every key point must be traceable to a specific detail in the provided evidence.
- Cover separate facts rather than rephrasing one claim.
- Include concrete implications only when the source supports them.
- Preserve exact companies, people, technologies, amounts, dates, and constraints.
- Do not add background, market framing, markdown, numbering, topics, quotes, or extra fields.
""",
    ),
    "decision_brief": (
        "Decision Brief",
        "Frames key points around whether the reader should open the item.",
        """You are a news editor writing a decision brief for a busy reader. Read the provided
evidence and produce a structured summary that helps the reader decide whether to open the item.

Field guidance:
- title: factual feed headline, <=95 characters.
- article_url: canonical article URL when available.
- key_points: include 3-4 distinct complete sentences, <=220 characters each; use 2 only for very sparse evidence.
- summary: required natural 2-3 sentence overview paragraph, 180-500 characters when possible.
- classification: use "to_read" when the item has concrete news, practical detail, or high discussion value; otherwise use "skip".

Rules:
- Key points should answer: what happened, why it matters now, who or what is affected, and what detail makes the item worth reading.
- Surface numbers, timelines, product names, policy changes, benchmark results, funding details, or technical constraints when present.
- Be explicit when the source is a claim, rumor, benchmark, opinion, or early report.
- Avoid generic phrases like "raises questions" unless the evidence names the question.
- Never include markdown, numbering, topics, quotes, or fields outside the schema.
""",
    ),
    "fact_dense": (
        "Fact Dense",
        "Maximizes concrete facts per key point while staying concise.",
        """You are a concise but fact-dense news summarizer. Read the article content and
aggregator context, then produce a structured output that gives the feed more useful key points.

Field guidance:
- title: concrete factual headline, <=95 characters.
- article_url: canonical article URL when available.
- key_points: produce 3-4 fact-dense, non-overlapping complete sentences, <=220 characters each; only use 2 when there are not enough grounded facts.
- summary: required 2-3 sentence overview paragraph, 180-500 characters when enough evidence exists.
- classification: use "to_read" for items with clear informational value and "skip" for thin, promotional, or duplicate material.

Rules:
- Each key point should contain at least one concrete noun, named entity, metric, event, product, technical term, date, or constraint when available.
- Remove filler and generic consequences; keep the strongest source-backed details.
- Use aggregator discussion only for additional context or reaction, not as a substitute for article evidence.
- Do not overstate certainty or infer motives not in the evidence.
- Never include markdown, numbering, topics, quotes, or fields outside the schema.
""",
    ),
}


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
        default=",".join(REPORT_MODEL_SPECS.keys()),
        help="Comma-separated model aliases from admin eval/report aliases.",
    )
    parser.add_argument(
        "--longform-template",
        type=str,
        choices=[
            "source_aware_editorial_v2",
            "long_bullets_v1",
            "interleaved_v2",
            "structured_v1",
            "editorial_narrative_v1",
        ],
        default="source_aware_editorial_v2",
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
        "--news-item-ids",
        type=str,
        default=None,
        help="Optional explicit news_items IDs, comma-separated. Only valid with --content-types news.",
    )
    parser.add_argument(
        "--news-snapshot-file",
        type=str,
        default=None,
        help=(
            "Optional JSON snapshot of production news_items, either a raw row list or an "
            "admin db query envelope. Only valid with --content-types news."
        ),
    )
    parser.add_argument(
        "--news-statuses",
        type=str,
        default=NewsItemStatus.READY.value,
        help=(
            "Comma-separated news_items statuses to sample, or 'all'. "
            f"Available: {', '.join(NEWS_STATUS_ORDER)}."
        ),
    )
    parser.add_argument(
        "--news-require-article-body",
        action="store_true",
        help="Skip news_items that cannot resolve full article body text.",
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
        choices=[
            "long_bullets",
            "interleaved",
            "structured",
            "editorial_narrative",
            "editorial_podcast",
            "editorial_substack",
            "editorial_twitter",
            "editorial_research",
            "editorial_github",
        ],
        default="editorial_narrative",
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
            "news",
            "long_bullets",
            "interleaved",
            "structured",
            "editorial_narrative",
        ],
        default="news",
        help="Output schema type to use when custom news prompts are configured.",
    )
    parser.add_argument(
        "--news-prompt-variants",
        type=str,
        default=None,
        help=(
            "Comma-separated built-in news prompt variants to compare, or 'all'. "
            f"Available: {', '.join(NEWS_PROMPT_VARIANT_ORDER)}. "
            "Only supported with --content-types news."
        ),
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


def parse_news_statuses(raw_value: str | None) -> list[str]:
    """Parse optional news item status filters."""
    if not raw_value:
        return [NewsItemStatus.READY.value]

    statuses = [item.lower() for item in parse_csv_list(raw_value)]
    if "all" in statuses:
        return list(NEWS_STATUS_ORDER)

    unknown = [status for status in statuses if status not in NEWS_STATUS_ORDER]
    if unknown:
        raise ValueError(
            "Unknown news statuses: "
            f"{', '.join(unknown)}. Available: {', '.join(NEWS_STATUS_ORDER)}"
        )
    if not statuses:
        raise ValueError("At least one news status is required")
    return statuses


def parse_news_prompt_variants(raw_value: str | None) -> list[str]:
    """Parse optional news prompt variant aliases."""
    if not raw_value:
        return []

    aliases = [item.lower() for item in parse_csv_list(raw_value)]
    if "all" in aliases:
        return list(NEWS_PROMPT_VARIANT_ORDER)

    unknown = [alias for alias in aliases if alias not in NEWS_PROMPT_VARIANT_ORDER]
    if unknown:
        raise ValueError(
            "Unknown news prompt variants: "
            f"{', '.join(unknown)}. Available: {', '.join(NEWS_PROMPT_VARIANT_ORDER)}"
        )
    if not aliases:
        raise ValueError("At least one news prompt variant is required")
    return aliases


def parse_datetime_value(value: Any) -> datetime | None:
    """Parse a JSON timestamp into a naive UTC-ish datetime for source ordering."""
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


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
    unknown = [alias for alias in models if alias not in REPORT_MODEL_SPECS]
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
    if not isinstance(value, (int, float, str, bytes, bytearray)):
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
    *,
    source_url: str | None = None,
) -> tuple[PromptType, int, int]:
    """Resolve default prompt settings matching eval behavior.

    Args:
        content_type: Content type for the row.
        longform_template: Long-form template selection.

    Returns:
        Tuple of prompt type, max bullet points, and max quotes.
    """
    if content_type == "news":
        return "news", 4, 0

    if longform_template == "source_aware_editorial_v2":
        prompt_type, max_bullet_points, max_quotes = resolve_summarization_prompt_route(
            content_type,
            url=source_url,
        )
        return cast(PromptType, prompt_type), max_bullet_points, max_quotes

    if longform_template == "interleaved_v2":
        return "interleaved", 8, 8
    if longform_template == "structured_v1":
        return "structured", 12, 8
    if longform_template == "editorial_narrative_v1":
        return "editorial_narrative", 10, 4
    return "long_bullets", 30, 3


def resolve_news_prompt_variant(alias: str) -> NewsPromptVariant:
    """Resolve one built-in news prompt variant."""
    if alias == "current":
        system_prompt, user_template = generate_summary_prompt(
            "news",
            max_bullet_points=4,
            max_quotes=0,
        )
        return NewsPromptVariant(
            alias="current",
            label="Current",
            description="Current production news prompt.",
            system_prompt=system_prompt,
            user_template=user_template,
            output_type="news",
        )

    variant = CUSTOM_NEWS_PROMPT_VARIANTS.get(alias)
    if variant is None:
        raise ValueError(f"Unknown news prompt variant: {alias}")

    label, description, system_prompt = variant
    return NewsPromptVariant(
        alias=alias,
        label=label,
        description=description,
        system_prompt=system_prompt,
        user_template=NEWS_PROMPT_VARIANT_USER_TEMPLATE,
        output_type="news",
    )


def resolve_prompt_for_source(
    *,
    content_type: EvalContentType,
    source_url: str | None,
    longform_template: LongformTemplate,
    custom_longform_system_prompt: str | None,
    custom_longform_user_template: str | None,
    custom_longform_output_type: PromptType,
    custom_news_system_prompt: str | None,
    custom_news_user_template: str | None,
    custom_news_output_type: PromptType,
    news_prompt_variant: str | None = None,
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
    if content_type == "news" and news_prompt_variant:
        variant = resolve_news_prompt_variant(news_prompt_variant)
        return variant.system_prompt, variant.user_template, variant.output_type

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
        source_url=source_url,
    )
    system_prompt, user_template = generate_summary_prompt(
        prompt_type,
        max_bullet_points=max_bullet_points,
        max_quotes=max_quotes,
    )
    return system_prompt, user_template, prompt_type


def resolve_available_models(
    models: list[str],
) -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
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
        model_spec = REPORT_MODEL_SPECS[alias]
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
        if provider == "openrouter" and not (
            settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
        ):
            skipped.append({"alias": alias, "reason": "OPENROUTER_API_KEY not configured"})
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
    news_prompt_variants: list[str],
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
        if content_type == "news" and news_prompt_variants:
            for alias in news_prompt_variants:
                variant = resolve_news_prompt_variant(alias)
                definitions.append(
                    {
                        "content_type": content_type,
                        "prompt_source": "builtin" if alias == "current" else "builtin_variant",
                        "prompt_type": variant.output_type,
                        "prompt_variant": variant.alias,
                        "prompt_variant_label": variant.label,
                        "prompt_variant_description": variant.description,
                        "system_prompt": variant.system_prompt,
                        "user_template": variant.user_template,
                    }
                )
            continue

        system_prompt, user_template, prompt_type = resolve_prompt_for_source(
            content_type=content_type,
            source_url=None,
            longform_template=longform_template,
            custom_longform_system_prompt=custom_longform_system_prompt,
            custom_longform_user_template=custom_longform_user_template,
            custom_longform_output_type=custom_longform_output_type,
            custom_news_system_prompt=custom_news_system_prompt,
            custom_news_user_template=custom_news_user_template,
            custom_news_output_type=custom_news_output_type,
            news_prompt_variant=None,
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


def build_news_item_eval_source_payload(
    db: Any,
    item: NewsItem,
    *,
    require_article_body: bool = False,
) -> EvalSourcePayload | None:
    """Build a report source payload from the current short-form ``NewsItem`` path."""
    news_item_id = item.id
    if news_item_id is None:
        return None

    raw_metadata = dict(item.raw_metadata or {})
    try:
        article_body_text = get_news_item_article_body_resolver().resolve_text(db, news_item=item)
    except FileNotFoundError:
        logger.warning(
            "News item article body missing from local storage; falling back to metadata",
            extra={
                "component": "eval_html_report",
                "operation": "build_news_item_eval_source_payload",
                "item_id": str(news_item_id),
            },
        )
        article_body_text = None
    if require_article_body and not article_body_text:
        logger.warning(
            "Skipping news item because no article body resolved",
            extra={
                "component": "eval_html_report",
                "operation": "build_news_item_eval_source_payload",
                "item_id": str(news_item_id),
            },
        )
        return None
    input_text = _build_processing_prompt(
        item,
        raw_metadata,
        article_body_text=article_body_text,
    )
    if not input_text.strip():
        return None

    created_at = item.ingested_at or item.created_at or item.published_at
    created_at_text = (
        created_at.replace(tzinfo=UTC).isoformat() if created_at else datetime.now(UTC).isoformat()
    )
    source_url = (
        item.article_url
        or item.canonical_story_url
        or item.canonical_item_url
        or item.discussion_url
        or "#"
    )
    source_title = item.article_title or item.summary_title

    return EvalSourcePayload(
        content_id=int(news_item_id),
        content_type="news",
        created_at=created_at_text,
        url=str(source_url),
        source_title=source_title,
        existing_summary_title=item.summary_title,
        existing_summary_key_points=normalize_snapshot_key_points(item.summary_key_points),
        existing_summary_text=clean_snapshot_text(item.summary_text),
        input_text=input_text,
        input_chars=len(input_text),
    )


def clean_snapshot_text(value: Any) -> str | None:
    """Normalize optional text from a production news snapshot."""
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def normalize_snapshot_key_points(value: Any) -> list[str]:
    """Return key point text from string or structured key-point rows."""
    if not isinstance(value, list):
        return []
    points: list[str] = []
    for raw in value:
        if isinstance(raw, dict):
            text = clean_snapshot_text(raw.get("text") or raw.get("point"))
        else:
            text = clean_snapshot_text(raw)
        if text:
            points.append(text)
    return points


def load_news_snapshot_rows(snapshot_file: str) -> list[dict[str, Any]]:
    """Load production news rows from a JSON snapshot or admin DB envelope."""
    payload = json.loads(Path(snapshot_file).read_text(encoding="utf-8"))
    rows: Any
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        data = payload.get("data")
        rows = data.get("rows") if isinstance(data, dict) else payload.get("rows")
    else:
        rows = None

    if not isinstance(rows, list):
        raise ValueError("News snapshot must be a row list or admin db query envelope")
    invalid_rows = [index for index, row in enumerate(rows, start=1) if not isinstance(row, dict)]
    if invalid_rows:
        raise ValueError(f"News snapshot includes non-object rows: {invalid_rows[:5]}")
    return cast(list[dict[str, Any]], rows)


def build_news_snapshot_eval_source_payload(row: dict[str, Any]) -> EvalSourcePayload | None:
    """Build a report source payload from one serialized production news_item row."""
    raw_id = row.get("id") or row.get("news_item_id")
    if raw_id is None:
        return None
    try:
        news_item_id = int(raw_id)
    except (TypeError, ValueError):
        return None

    raw_metadata = row.get("raw_metadata")
    if isinstance(raw_metadata, str):
        try:
            raw_metadata = json.loads(raw_metadata)
        except json.JSONDecodeError:
            raw_metadata = {}
    if not isinstance(raw_metadata, dict):
        raw_metadata = {}

    article_title = clean_snapshot_text(row.get("article_title"))
    if article_title is None:
        article = raw_metadata.get("article")
        if isinstance(article, dict):
            article_title = clean_snapshot_text(article.get("title"))
    summary_title = clean_snapshot_text(row.get("summary_title"))
    if summary_title is None:
        summary = raw_metadata.get("summary")
        if isinstance(summary, dict):
            summary_title = clean_snapshot_text(summary.get("title"))

    item = SimpleNamespace(
        id=news_item_id,
        source_label=clean_snapshot_text(row.get("source_label")),
        platform=clean_snapshot_text(row.get("platform")),
        article_title=article_title,
        article_domain=clean_snapshot_text(row.get("article_domain")),
        article_url=clean_snapshot_text(row.get("article_url")),
        canonical_story_url=clean_snapshot_text(row.get("canonical_story_url")),
        canonical_item_url=clean_snapshot_text(row.get("canonical_item_url")),
        discussion_url=clean_snapshot_text(row.get("discussion_url")),
        summary_title=summary_title,
        summary_key_points=normalize_snapshot_key_points(row.get("summary_key_points")),
        summary_text=clean_snapshot_text(row.get("summary_text")),
        raw_metadata=raw_metadata,
        ingested_at=parse_datetime_value(row.get("ingested_at")),
        created_at=parse_datetime_value(row.get("created_at")),
        published_at=parse_datetime_value(row.get("published_at")),
    )
    article_body_text = clean_snapshot_text(row.get("article_body_text"))
    input_text = _build_processing_prompt(
        cast(NewsItem, item),
        raw_metadata,
        article_body_text=article_body_text,
    )
    if not input_text.strip():
        return None

    created_at = item.ingested_at or item.created_at or item.published_at
    created_at_text = (
        created_at.replace(tzinfo=UTC).isoformat() if created_at else datetime.now(UTC).isoformat()
    )
    source_url = (
        item.article_url
        or item.canonical_story_url
        or item.canonical_item_url
        or item.discussion_url
        or "#"
    )
    return EvalSourcePayload(
        content_id=news_item_id,
        content_type="news",
        created_at=created_at_text,
        url=str(source_url),
        source_title=item.article_title or item.summary_title,
        existing_summary_title=item.summary_title,
        existing_summary_key_points=list(item.summary_key_points),
        existing_summary_text=item.summary_text,
        input_text=input_text,
        input_chars=len(input_text),
    )


def select_news_snapshot_eval_sources(
    *,
    snapshot_file: str,
    sample_size: int,
) -> tuple[list[EvalSourcePayload], list[int]]:
    """Select eval sources from a serialized production news snapshot."""
    rows = load_news_snapshot_rows(snapshot_file)
    payloads = [
        payload
        for row in rows
        if (payload := build_news_snapshot_eval_source_payload(row)) is not None
    ]
    return payloads[: min(sample_size, len(payloads))], []


def select_news_item_eval_sources(
    *,
    news_item_ids: list[int],
    news_statuses: list[str],
    require_article_body: bool,
    recent_pool_size: int,
    sample_size: int,
    seed: int | None,
) -> tuple[list[EvalSourcePayload], list[int]]:
    """Select current short-form news items for news prompt comparison."""
    rng = random.Random(seed)
    with get_db() as db:
        if news_item_ids:
            rows = db.query(NewsItem).filter(NewsItem.id.in_(news_item_ids)).all()
            row_by_id = {row.id: row for row in rows}
            payloads: list[EvalSourcePayload] = []
            missing_ids: list[int] = []
            for news_item_id in news_item_ids:
                row = row_by_id.get(news_item_id)
                if row is None:
                    missing_ids.append(news_item_id)
                    continue
                payload = build_news_item_eval_source_payload(
                    db,
                    row,
                    require_article_body=require_article_body,
                )
                if payload is not None:
                    payloads.append(payload)
            return payloads, missing_ids

        rows = (
            db.query(NewsItem)
            .filter(NewsItem.status.in_(news_statuses))
            .order_by(desc(NewsItem.ingested_at))
            .limit(recent_pool_size)
            .all()
        )
        payloads = [
            payload
            for row in rows
            if (
                payload := build_news_item_eval_source_payload(
                    db,
                    row,
                    require_article_body=require_article_body,
                )
            )
            is not None
        ]

    rng.shuffle(payloads)
    return payloads[: min(sample_size, len(payloads))], []


def select_sources(
    *,
    content_ids: list[int],
    news_item_ids: list[int],
    news_snapshot_file: str | None,
    news_statuses: list[str],
    news_require_article_body: bool,
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
    if news_snapshot_file:
        if content_types != ["news"]:
            raise ValueError("--news-snapshot-file is only supported with --content-types news")
        if content_ids or news_item_ids:
            raise ValueError("--news-snapshot-file cannot be combined with explicit IDs")
        return select_news_snapshot_eval_sources(
            snapshot_file=news_snapshot_file,
            sample_size=sample_size,
        )

    if not content_ids and content_types == ["news"]:
        return select_news_item_eval_sources(
            news_item_ids=news_item_ids,
            news_statuses=news_statuses,
            require_article_body=news_require_article_body,
            recent_pool_size=recent_pool_size,
            sample_size=sample_size,
            seed=seed,
        )

    if news_item_ids:
        raise ValueError("--news-item-ids is only supported with --content-types news")

    with get_db() as db:
        if content_ids:
            rows = db.query(Content).filter(Content.id.in_(content_ids)).all()
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
    output_type = resolve_summarization_output_type(prompt_type)
    return get_basic_agent(model_spec, output_type, system_prompt)


def build_model_run_settings(model_alias: str, timeout_seconds: int) -> dict[str, Any]:
    """Build per-run model settings for report-only model variants."""
    model_settings: dict[str, Any] = {"timeout": timeout_seconds}
    for key, value in REPORT_MODEL_SETTINGS_BY_ALIAS.get(model_alias, {}).items():
        model_settings[key] = value.copy() if isinstance(value, dict) else value
    return model_settings


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
    news_prompt_variant: str | None,
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
        news_prompt_variant: Optional built-in news prompt variant alias.

    Returns:
        Model cell result for JSON + HTML rendering.
    """
    system_prompt, user_template, prompt_type = resolve_prompt_for_source(
        content_type=source.content_type,
        source_url=source.url,
        longform_template=longform_template,
        custom_longform_system_prompt=custom_longform_system_prompt,
        custom_longform_user_template=custom_longform_user_template,
        custom_longform_output_type=custom_longform_output_type,
        custom_news_system_prompt=custom_news_system_prompt,
        custom_news_user_template=custom_news_user_template,
        custom_news_output_type=custom_news_output_type,
        news_prompt_variant=news_prompt_variant,
    )
    if "{content}" not in user_template:
        raise ValueError("User template must include a {content} placeholder")

    clipped_input = clip_eval_input(source.input_text, max_input_chars)
    title_prefix = f"Title: {source.source_title}\n\n" if source.source_title else ""
    user_message = user_template.format(content=f"{title_prefix}{clipped_input}")

    prompt_variant_label = None
    prompt_variant_description = None
    if news_prompt_variant:
        variant = resolve_news_prompt_variant(news_prompt_variant)
        prompt_variant_label = variant.label
        prompt_variant_description = variant.description

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
                model_settings=build_model_run_settings(model_alias, timeout_seconds),
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            usage = extract_usage(result)
            payload = extract_result_payload(result)
            output_chars = len(json.dumps(payload, ensure_ascii=False))

            logger.info(
                "Eval success content_id=%s model=%s prompt_variant=%s attempt=%s latency_ms=%s req_chars=%s",
                source.content_id,
                model_alias,
                news_prompt_variant or "default",
                attempt,
                latency_ms,
                request_chars,
            )
            model_label = REPORT_MODEL_LABELS.get(model_alias, model_alias)
            if prompt_variant_label:
                model_label = f"{prompt_variant_label} · {model_label}"
            return {
                "model_alias": model_alias,
                "model_label": model_label,
                "model_spec": model_spec,
                "status": "ok",
                "attempt": attempt,
                "prompt_type": prompt_type,
                "prompt_variant": news_prompt_variant,
                "prompt_variant_label": prompt_variant_label,
                "prompt_variant_description": prompt_variant_description,
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
                "Eval failure content_id=%s model=%s prompt_variant=%s attempt=%s/%s latency_ms=%s req_chars=%s error=%s",
                source.content_id,
                model_alias,
                news_prompt_variant or "default",
                attempt,
                attempts,
                latency_ms,
                request_chars,
                str(exc),
            )
            if attempt < attempts:
                time.sleep(retry_backoff_seconds * attempt)

    assert last_error is not None
    model_label = REPORT_MODEL_LABELS.get(model_alias, model_alias)
    if prompt_variant_label:
        model_label = f"{prompt_variant_label} · {model_label}"
    return {
        "model_alias": model_alias,
        "model_label": model_label,
        "model_spec": model_spec,
        "status": "error",
        "attempt": attempts,
        "prompt_type": prompt_type,
        "prompt_variant": news_prompt_variant,
        "prompt_variant_label": prompt_variant_label,
        "prompt_variant_description": prompt_variant_description,
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


def _render_source_details(source_details: Any) -> str:
    """Render source-aware editorial detail fields."""
    if not isinstance(source_details, dict) or not source_details:
        return ""

    rows: list[str] = []
    for key, value in source_details.items():
        label = key.replace("_", " ").title()
        if isinstance(value, list):
            items = _collect_text_items(value, keys=("text", "point"))
            rendered = _render_string_list(items, class_name="source-detail-list")
        elif isinstance(value, dict):
            rendered = _render_source_details(value)
        else:
            rendered = f"<p>{html_escape(str(value))}</p>" if value is not None else ""
        if rendered:
            rows.append(
                f"""
                <div class="source-detail-row">
                  <h6>{html_escape(label)}</h6>
                  {rendered}
                </div>
                """
            )

    if not rows:
        return ""
    return f'<div class="source-details">{"".join(rows)}</div>'


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
        source_details_html = _render_source_details(payload.get("source_details"))
        if source_details_html:
            blocks.append(
                f"""
                <section class="output-section">
                  <h6>Source Details</h6>
                  {source_details_html}
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
    summary_text = _get_text(payload, keys=("summary", "overview", "takeaway"))
    if summary_text:
        blocks.append(
            f"""
            <section class="output-section summary-output">
              <h6>Summary</h6>
              {_render_paragraphs(summary_text)}
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

    def _prompt_title(prompt: dict[str, Any]) -> str:
        content_type = str(prompt.get("content_type", "unknown"))
        prompt_type = str(prompt.get("prompt_type", "unknown"))
        variant_label = prompt.get("prompt_variant_label")
        if variant_label:
            return f"{content_type} · {variant_label} · {prompt_type}"
        return f"{content_type} · {prompt_type}"

    model_names = ", ".join(f"{m['label']} ({m['alias']})" for m in models) or "None"
    skipped_text = (
        " | ".join(f"{item['alias']}: {item['reason']}" for item in skipped_models)
        if skipped_models
        else "None"
    )
    prompt_cards = "".join(
        f"""
        <article class="prompt-card">
          <h3>{html_escape(_prompt_title(prompt))}</h3>
          <p class="detail"><strong>Source:</strong> {html_escape(str(prompt.get("prompt_source", "unknown")))}</p>
          <p class="detail">{html_escape(str(prompt.get("prompt_variant_description", "")))}</p>
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
            prompt_variant_label = str(
                cell.get("prompt_variant_label") or cell.get("prompt_variant") or "Default"
            )
            key_point_count = len(
                _collect_text_items(output.get("key_points"), keys=("text", "point"))
            )
            model_columns.append(
                f"""
                <article class="model-card ok">
                  <header>
                    <div>
                      <h4>{html_escape(prompt_variant_label)}</h4>
                      <p class="key-point-count">{key_point_count} key points</p>
                    </div>
                  </header>
                  <section class="output-body">
                    {_render_output_payload(output)}
                  </section>
                  <details class="run-details">
                    <summary>Run details</summary>
                    <dl class="metrics">
                      <div><dt>Status</dt><dd>{html_escape(cell["status"])}</dd></div>
                      <div><dt>Model</dt><dd>{html_escape(cell["model_spec"])}</dd></div>
                      <div><dt>Attempt</dt><dd>{cell["attempt"]}</dd></div>
                      <div><dt>Prompt</dt><dd>{html_escape(str(cell.get("prompt_type", "unknown")))}</dd></div>
                      <div><dt>Latency</dt><dd>{cell["latency_ms"] if cell["latency_ms"] is not None else "n/a"} ms</dd></div>
                      <div><dt>Input Tokens</dt><dd>{usage.get("input_tokens") if usage.get("input_tokens") is not None else "n/a"}</dd></div>
                      <div><dt>Output Tokens</dt><dd>{usage.get("output_tokens") if usage.get("output_tokens") is not None else "n/a"}</dd></div>
                      <div><dt>Total Tokens</dt><dd>{usage.get("total_tokens") if usage.get("total_tokens") is not None else "n/a"}</dd></div>
                      <div><dt>Request Chars</dt><dd>{cell["request_chars"]}</dd></div>
                      <div><dt>Req Tokens (est)</dt><dd>{cell["request_tokens_estimate"]}</dd></div>
                      <div><dt>Output Chars</dt><dd>{cell["output_chars"]}</dd></div>
                    </dl>
                  </details>
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
        existing_points = item.get("existing_summary_key_points") or []
        existing_summary_text = str(item.get("existing_summary_text") or "").strip()
        existing_summary_title = str(item.get("existing_summary_title") or "").strip()
        existing_summary_block = ""
        if existing_summary_title or existing_summary_text or existing_points:
            existing_summary_block = f"""
              <section class="existing-summary">
                <h4>Current Live Key Points</h4>
                {f"<h5>{html_escape(existing_summary_title)}</h5>" if existing_summary_title else ""}
                {_render_string_list(cast(list[str], existing_points), class_name="output-list") if existing_points else ""}
                {f"<details><summary>Live summary text</summary>{_render_paragraphs(existing_summary_text)}</details>" if existing_summary_text else ""}
              </section>
            """
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
              {existing_summary_block}
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
      background: var(--bg);
    }}
    .container {{ max-width: 1600px; margin: 0 auto; padding: 24px; }}
    .summary {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px;
      margin-bottom: 18px;
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
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 14px;
    }}
    .content-header h3 {{ margin: 8px 0; font-size: 20px; }}
    .content-header a {{ color: var(--accent); text-decoration: none; word-break: break-all; }}
    .existing-summary {{
      margin-top: 12px;
      border: 1px solid #d0d5dd;
      border-radius: 10px;
      padding: 12px;
      background: #f8fafc;
      display: grid;
      gap: 8px;
    }}
    .existing-summary h4 {{
      margin: 0;
      font-size: 13px;
      color: var(--muted);
    }}
    .existing-summary h5 {{ margin: 0; font-size: 16px; }}
    .existing-summary p {{ margin: 0; font-size: 14px; line-height: 1.45; }}
    .meta {{ display: flex; gap: 8px; flex-wrap: wrap; color: var(--muted); font-size: 12px; }}
    .pill {{
      border: 1px solid #84adff;
      color: #1849a9;
      border-radius: 6px;
      padding: 2px 8px;
      font-weight: 600;
      background: #eff8ff;
    }}
    .model-grid {{
      margin-top: 14px;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }}
    .model-card {{
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
    }}
    .model-card.ok {{ background: #fff; border-color: var(--border); }}
    .model-card.error {{ background: var(--error-bg); border-color: #fda29b; }}
    .model-card h4 {{ margin: 0; font-size: 16px; }}
    .key-point-count {{
      margin: 3px 0 0;
      color: var(--muted);
      font-size: 12px;
    }}
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
      grid-template-columns: minmax(0, 1fr);
      gap: 6px 10px;
    }}
    .metrics div {{
      display: grid;
      grid-template-columns: 120px minmax(0, 1fr);
      gap: 8px;
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
      .model-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 640px) {{
      .model-grid {{ grid-template-columns: 1fr; }}
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
      <div class="grid">
        <div class="stat"><div class="label">Items</div><div class="value">{aggregate["items_total"]}</div></div>
        <div class="stat"><div class="label">Cells Total</div><div class="value">{aggregate["cells_total"]}</div></div>
        <div class="stat"><div class="label">Cells Success</div><div class="value">{aggregate["cells_successful"]}</div></div>
        <div class="stat"><div class="label">Cells Failed</div><div class="value">{aggregate["cells_failed"]}</div></div>
      </div>
      <details class="run-details">
        <summary>Run config</summary>
        <p class="detail">content_types={html_escape(",".join(config["content_types"]))}, sample_size={config["sample_size"]}, recent_pool_size={config["recent_pool_size"]}, longform_template={html_escape(config["longform_template"])}, news_prompt_variants={html_escape(",".join(config.get("news_prompt_variants") or [])) or "default"}, news_statuses={html_escape(",".join(config.get("news_statuses") or [])) or "default"}, require_article_body={html_escape(str(config.get("news_require_article_body", False)))}, seed={html_escape(str(config["seed"]))}</p>
      </details>
      <details class="prompt-section">
        <summary>Prompts used</summary>
        <div class="prompt-wrap">
          {prompt_cards}
        </div>
      </details>
    </section>
    {"".join(sections)}
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

    try:
        content_types = validate_content_types(parse_csv_list(args.content_types))
        models = validate_models(parse_csv_list(args.models))
        content_ids = parse_content_ids(args.content_ids)
        news_item_ids = parse_content_ids(args.news_item_ids)
        news_statuses = parse_news_statuses(args.news_statuses)
        news_prompt_variants = parse_news_prompt_variants(args.news_prompt_variants)
        if news_prompt_variants and content_types != ["news"]:
            raise ValueError("--news-prompt-variants is only supported with --content-types news")
        if news_item_ids and content_types != ["news"]:
            raise ValueError("--news-item-ids is only supported with --content-types news")
        if news_item_ids and content_ids:
            raise ValueError("--news-item-ids cannot be combined with --content-ids")
        if args.news_snapshot_file and content_types != ["news"]:
            raise ValueError("--news-snapshot-file is only supported with --content-types news")
        if args.news_snapshot_file and (news_item_ids or content_ids):
            raise ValueError("--news-snapshot-file cannot be combined with explicit IDs")
        if args.news_snapshot_file and args.news_require_article_body:
            raise ValueError("--news-require-article-body is only supported for DB-backed sources")
        if news_prompt_variants and (
            args.custom_news_system_prompt_file or args.custom_news_user_template_file
        ):
            raise ValueError(
                "--news-prompt-variants cannot be combined with custom news prompt files"
            )
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

    if not args.news_snapshot_file:
        init_db()
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
        news_prompt_variants=news_prompt_variants,
    )
    selected_sources, missing_ids = select_sources(
        content_ids=content_ids,
        news_item_ids=news_item_ids,
        news_snapshot_file=args.news_snapshot_file,
        news_statuses=news_statuses,
        news_require_article_body=args.news_require_article_body,
        content_types=content_types,
        recent_pool_size=args.recent_pool_size,
        sample_size=args.sample_size,
        seed=args.seed,
    )
    if missing_ids:
        missing_label = "news item" if news_item_ids else "content"
        logger.warning(
            "Missing %s IDs: %s",
            missing_label,
            ",".join(str(item) for item in missing_ids),
        )

    logger.info(
        "Generating report for items=%s models=%s timeout=%ss retries=%s",
        len(selected_sources),
        len(available_models),
        args.timeout_seconds,
        args.max_retries,
    )

    item_results: list[dict[str, Any]] = []
    model_spec_map = dict(available_models)
    ordered_aliases = [alias for alias in models if alias in model_spec_map]
    model_pairs = [(alias, model_spec_map[alias]) for alias in ordered_aliases]

    for source in selected_sources:
        model_results: list[dict[str, Any]] = []
        for alias, model_spec in model_pairs:
            source_news_variants: list[str | None] = (
                list(news_prompt_variants)
                if source.content_type == "news" and news_prompt_variants
                else [None]
            )
            for news_prompt_variant in source_news_variants:
                model_results.append(
                    run_single_model_call(
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
                        news_prompt_variant=news_prompt_variant,
                    )
                )

        item_results.append(
            {
                "content_id": source.content_id,
                "content_type": source.content_type,
                "created_at": source.created_at,
                "url": source.url,
                "source_title": source.source_title,
                "existing_summary_title": source.existing_summary_title,
                "existing_summary_key_points": source.existing_summary_key_points,
                "existing_summary_text": source.existing_summary_text,
                "input_chars": source.input_chars,
                "model_results": model_results,
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
            "news_item_ids": news_item_ids,
            "news_snapshot_file": args.news_snapshot_file,
            "news_statuses": news_statuses,
            "news_require_article_body": args.news_require_article_body,
            "timeout_seconds": args.timeout_seconds,
            "max_retries": args.max_retries,
            "max_input_chars": args.max_input_chars,
            "custom_longform_prompt_enabled": bool(custom_longform_system_prompt),
            "custom_news_prompt_enabled": bool(custom_news_system_prompt),
            "news_prompt_variants": news_prompt_variants,
        },
        "available_models": [
            {
                "alias": alias,
                "label": REPORT_MODEL_LABELS.get(alias, alias),
                "model_spec": model_spec,
            }
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
