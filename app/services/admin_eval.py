"""Admin-only LLM eval helpers for summary and title comparison."""

from __future__ import annotations

import json
import logging
import math
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.schema import Content
from app.services.llm_agents import get_basic_agent
from app.services.llm_prompts import generate_summary_prompt
from app.services.llm_summarization import resolve_summarization_output_type

logger = logging.getLogger(__name__)

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

DEFAULT_CONTENT_TYPES: list[EvalContentType] = ["article", "podcast", "news"]
MAX_EVAL_INPUT_CHARS = 120_000
EVAL_CALL_TIMEOUT_SECONDS = 15
# Keep eval calls sequential to avoid cross-event-loop/client instability
# observed when multiple pydantic-ai model clients run concurrently.
EVAL_MAX_PARALLEL_MODEL_CALLS = 1
ESTIMATED_CHARS_PER_TOKEN = 4


class ModelPricing(BaseModel):
    """Optional pricing inputs for estimated cost calculations."""

    input_per_million_usd: float | None = Field(default=None, ge=0)
    output_per_million_usd: float | None = Field(default=None, ge=0)


class AdminEvalRunRequest(BaseModel):
    """Request payload for running an admin eval batch."""

    content_types: list[EvalContentType] = Field(
        default_factory=lambda: list(DEFAULT_CONTENT_TYPES)
    )
    models: list[str] = Field(default_factory=lambda: list(EVAL_MODEL_SPECS.keys()))
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

        unknown = [model for model in deduped if model not in EVAL_MODEL_SPECS]
        if unknown:
            raise ValueError(f"Unknown model aliases: {', '.join(unknown)}")
        return deduped

    @model_validator(mode="after")
    def validate_sample_bounds(self) -> AdminEvalRunRequest:
        """Ensure sample size does not exceed pool size."""
        if self.sample_size > self.recent_pool_size:
            raise ValueError("sample_size must be <= recent_pool_size")
        return self


class EvalSourcePayload(BaseModel):
    """Normalized source input used by the eval runner."""

    content_id: int
    content_type: EvalContentType
    created_at: str
    url: str
    source_title: str | None
    existing_summary_title: str | None
    input_text: str
    input_chars: int


def get_default_pricing() -> dict[str, dict[str, float | None]]:
    """Return empty pricing defaults for each known eval model alias."""
    return {
        alias: {"input_per_million_usd": None, "output_per_million_usd": None}
        for alias in EVAL_MODEL_SPECS
    }


def select_eval_samples(
    db: Session,
    *,
    content_types: list[EvalContentType],
    recent_pool_size: int,
    sample_size: int,
    seed: int | None,
) -> dict[EvalContentType, list[EvalSourcePayload]]:
    """Select random samples from the latest content rows by type.

    Args:
        db: Database session.
        content_types: Selected content types.
        recent_pool_size: Latest row window to sample from.
        sample_size: Total number of random rows across selected content types.
        seed: Optional deterministic seed.

    Returns:
        Mapping of content type to selected source payloads.
    """
    rng = random.Random(seed)
    selected: dict[EvalContentType, list[EvalSourcePayload]] = {
        content_type: [] for content_type in content_types
    }

    pools: dict[EvalContentType, list[EvalSourcePayload]] = {}
    for content_type in content_types:
        rows = (
            db.query(Content)
            .filter(Content.status == "completed")
            .filter(Content.content_type == content_type)
            .order_by(desc(Content.created_at))
            .limit(recent_pool_size)
            .all()
        )

        normalized_rows: list[EvalSourcePayload] = []
        for row in rows:
            payload = build_eval_source_payload(row)
            if payload is not None:
                normalized_rows.append(payload)

        if not normalized_rows:
            pools[content_type] = []
            continue

        pools[content_type] = normalized_rows.copy()
        rng.shuffle(pools[content_type])

    total_available = sum(len(pool) for pool in pools.values())
    total_to_select = min(sample_size, total_available)
    if total_to_select <= 0:
        return selected

    pool_order = [content_type for content_type in content_types if pools.get(content_type)]
    if not pool_order:
        return selected

    # Round-robin picks to keep a balanced mix across selected content types.
    while total_to_select > 0 and pool_order:
        next_round: list[EvalContentType] = []
        for content_type in pool_order:
            pool = pools.get(content_type, [])
            if not pool:
                continue
            selected[content_type].append(pool.pop())
            total_to_select -= 1
            if total_to_select <= 0:
                break
            if pool:
                next_round.append(content_type)
        pool_order = next_round

    return selected


def run_admin_eval(db: Session, request: AdminEvalRunRequest) -> dict[str, Any]:
    """Run eval calls across selected content samples and models.

    Args:
        db: Database session.
        request: Eval run request.

    Returns:
        JSON-serializable run output for admin UI rendering.
    """
    run_started_at = datetime.now(UTC)
    logger.info(
        "Starting admin eval run",
        extra={
            "component": "admin_eval",
            "operation": "run_start",
            "context_data": {
                "content_types": request.content_types,
                "models": request.models,
                "recent_pool_size": request.recent_pool_size,
                "sample_size": request.sample_size,
                "seed": request.seed,
                "longform_template": request.longform_template,
                "parallel_model_calls": EVAL_MAX_PARALLEL_MODEL_CALLS,
            },
        },
    )
    available_models, skipped_models = _resolve_model_availability(request.models)
    active_models: dict[str, str] = dict(available_models)
    runtime_skipped_models: list[dict[str, str]] = []
    sample_map = select_eval_samples(
        db,
        content_types=request.content_types,
        recent_pool_size=request.recent_pool_size,
        sample_size=request.sample_size,
        seed=request.seed,
    )
    sample_counts = {
        content_type: len(sample_map[content_type]) for content_type in request.content_types
    }
    logger.info(
        "Selected admin eval samples",
        extra={
            "component": "admin_eval",
            "operation": "sample_selection",
            "context_data": {
                "sample_counts": sample_counts,
                "available_models": [alias for alias, _ in available_models],
                "pre_run_skipped_models": skipped_models,
            },
        },
    )

    item_results: list[dict[str, Any]] = []
    for content_type in request.content_types:
        for source in sample_map[content_type]:
            active_ordered = [
                (alias, active_models[alias]) for alias in request.models if alias in active_models
            ]
            model_results_by_alias: dict[str, dict[str, Any]] = {}
            worker_count = min(EVAL_MAX_PARALLEL_MODEL_CALLS, len(active_ordered))
            if worker_count <= 1:
                for alias, spec in active_ordered:
                    result = _run_single_model_eval(
                        source=source,
                        request=request,
                        model_alias=alias,
                        model_spec=spec,
                    )
                    model_results_by_alias[alias] = result
            else:
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    futures = {
                        executor.submit(
                            _run_single_model_eval,
                            source=source,
                            request=request,
                            model_alias=alias,
                            model_spec=spec,
                        ): alias
                        for alias, spec in active_ordered
                    }
                    for future in as_completed(futures):
                        alias = futures[future]
                        model_results_by_alias[alias] = future.result()

            model_results = [model_results_by_alias[alias] for alias, _ in active_ordered]
            for alias, result in model_results_by_alias.items():
                if result.get("status") != "error":
                    continue
                error_text = str(result.get("error") or "")
                if not _should_disable_model_after_error(error_text):
                    continue
                active_models.pop(alias, None)
                runtime_skipped_models.append(
                    {
                        "alias": alias,
                        "reason": f"disabled_after_error: {error_text[:180]}",
                    }
                )
                logger.error(
                    "Disabling eval model after hard error",
                    extra={
                        "component": "admin_eval",
                        "operation": "model_disabled_after_error",
                        "item_id": source.content_id,
                        "context_data": {
                            "content_type": source.content_type,
                            "model_alias": alias,
                            "reason": error_text[:180],
                        },
                    },
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
                    "model_results": model_results,
                }
            )

    aggregate = _build_aggregate_metrics(item_results)
    run_output = {
        "run_started_at": run_started_at.isoformat(),
        "run_completed_at": datetime.now(UTC).isoformat(),
        "config": {
            "content_types": request.content_types,
            "models": request.models,
            "longform_template": request.longform_template,
            "recent_pool_size": request.recent_pool_size,
            "sample_size": request.sample_size,
            "seed": request.seed,
        },
        "available_models": [
            {"alias": alias, "label": EVAL_MODEL_LABELS.get(alias, alias), "model_spec": spec}
            for alias, spec in available_models
        ],
        "skipped_models": skipped_models + runtime_skipped_models,
        "samples_by_type": {
            content_type: [
                {
                    "content_id": source.content_id,
                    "created_at": source.created_at,
                    "url": source.url,
                    "source_title": source.source_title,
                }
                for source in sample_map[content_type]
            ]
            for content_type in request.content_types
        },
        "results": item_results,
        "aggregate": aggregate,
    }
    logger.info(
        "Completed admin eval run",
        extra={
            "component": "admin_eval",
            "operation": "run_complete",
            "context_data": {
                "items_total": aggregate.get("items_total"),
                "cells_total": aggregate.get("cells_total"),
                "cells_successful": aggregate.get("cells_successful"),
                "cells_failed": aggregate.get("cells_failed"),
                "avg_latency_ms": aggregate.get("avg_latency_ms"),
                "avg_request_chars": aggregate.get("avg_request_chars"),
                "avg_request_tokens_estimate": aggregate.get("avg_request_tokens_estimate"),
                "avg_request_tokens_actual": aggregate.get("avg_request_tokens_actual"),
                "skipped_models_total": len(skipped_models) + len(runtime_skipped_models),
            },
        },
    )
    return run_output


def build_eval_source_payload(content: Content) -> EvalSourcePayload | None:
    """Extract a normalized source payload from a content row.

    Args:
        content: Content ORM row.

    Returns:
        EvalSourcePayload when text input is available, else None.
    """
    metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
    input_text = _extract_input_text(content.content_type, metadata)
    if not input_text:
        return None

    summary = metadata.get("summary") if isinstance(metadata, dict) else None
    existing_summary_title = summary.get("title") if isinstance(summary, dict) else None
    created_at = (
        content.created_at.replace(tzinfo=UTC).isoformat()
        if content.created_at
        else datetime.now(UTC).isoformat()
    )

    return EvalSourcePayload(
        content_id=content.id,
        content_type=content.content_type,
        created_at=created_at,
        url=str(content.url),
        source_title=content.title,
        existing_summary_title=existing_summary_title,
        input_text=input_text,
        input_chars=len(input_text),
    )


def _run_single_model_eval(
    *,
    source: EvalSourcePayload,
    request: AdminEvalRunRequest,
    model_alias: str,
    model_spec: str,
) -> dict[str, Any]:
    prompt_type, max_bullet_points, max_quotes = _resolve_prompt_settings(
        source.content_type,
        request,
    )
    system_prompt, user_template = generate_summary_prompt(
        prompt_type,
        max_bullet_points=max_bullet_points,
        max_quotes=max_quotes,
    )

    title_prefix = f"Title: {source.source_title}\n\n" if source.source_title else ""
    clipped_text = _clip_eval_input(source.input_text)
    user_message = user_template.format(content=f"{title_prefix}{clipped_text}")
    request_chars = len(system_prompt) + len(user_message)
    request_tokens_estimate = _estimate_tokens_from_chars(request_chars)
    started = time.perf_counter()
    logger.info(
        "Starting admin eval model call content_id=%s "
        "model_alias=%s prompt_type=%s request_chars=%s",
        source.content_id,
        model_alias,
        prompt_type,
        request_chars,
        extra={
            "component": "admin_eval",
            "operation": "model_call_start",
            "item_id": source.content_id,
            "context_data": {
                "content_type": source.content_type,
                "model_alias": model_alias,
                "model_spec": model_spec,
                "prompt_type": prompt_type,
                "input_chars": source.input_chars,
                "clipped_input_chars": len(clipped_text),
                "request_chars": request_chars,
                "request_tokens_estimate": request_tokens_estimate,
                "timeout_seconds": EVAL_CALL_TIMEOUT_SECONDS,
            },
        },
    )

    try:
        output_type = resolve_summarization_output_type(prompt_type)
        agent = get_basic_agent(model_spec, output_type, system_prompt)
        result = agent.run_sync(
            user_message,
            model_settings={"timeout": EVAL_CALL_TIMEOUT_SECONDS},
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        payload = _extract_result_payload(result)
        generated_title = payload.get("title") if isinstance(payload, dict) else None
        output_chars = len(json.dumps(payload, ensure_ascii=False))
        usage = _extract_usage(result)
        request_tokens_actual = usage.get("input_tokens")
        pricing = request.pricing.get(model_alias)
        estimated_cost_usd, cost_reason = _estimate_cost(usage, pricing)

        raw_output = payload
        display_output: dict[str, Any] = (
            {"title": generated_title} if source.content_type == "news" else payload
        )

        logger.info(
            "Completed admin eval model call content_id=%s "
            "model_alias=%s latency_ms=%s request_chars=%s",
            source.content_id,
            model_alias,
            latency_ms,
            request_chars,
            extra={
                "component": "admin_eval",
                "operation": "model_call_success",
                "item_id": source.content_id,
                "context_data": {
                    "content_type": source.content_type,
                    "model_alias": model_alias,
                    "model_spec": model_spec,
                    "prompt_type": prompt_type,
                    "latency_ms": latency_ms,
                    "request_chars": request_chars,
                    "request_tokens_estimate": request_tokens_estimate,
                    "request_tokens_actual": request_tokens_actual,
                    "output_chars": output_chars,
                    "output_tokens_actual": usage.get("output_tokens"),
                },
            },
        )
        return {
            "model_alias": model_alias,
            "model_label": EVAL_MODEL_LABELS.get(model_alias, model_alias),
            "model_spec": model_spec,
            "status": "ok",
            "error": None,
            "latency_ms": latency_ms,
            "usage": usage,
            "estimated_cost_usd": estimated_cost_usd,
            "cost_reason": cost_reason,
            "generated_title": generated_title,
            "title_chars": len(generated_title or ""),
            "request_chars": request_chars,
            "request_tokens_estimate": request_tokens_estimate,
            "request_tokens_actual": request_tokens_actual,
            "output_chars": output_chars,
            "display_output": display_output,
            "raw_output": raw_output,
            "prompt_type": prompt_type,
        }
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "Admin eval model call failed content_id=%s "
            "model_alias=%s latency_ms=%s request_chars=%s",
            source.content_id,
            model_alias,
            latency_ms,
            request_chars,
            extra={
                "component": "admin_eval",
                "operation": "model_call_error",
                "item_id": source.content_id,
                "context_data": {
                    "content_type": source.content_type,
                    "model_alias": model_alias,
                    "model_spec": model_spec,
                    "prompt_type": prompt_type,
                    "latency_ms": latency_ms,
                    "request_chars": request_chars,
                    "request_tokens_estimate": request_tokens_estimate,
                    "input_chars": source.input_chars,
                    "error": str(exc),
                },
            },
        )
        return {
            "model_alias": model_alias,
            "model_label": EVAL_MODEL_LABELS.get(model_alias, model_alias),
            "model_spec": model_spec,
            "status": "error",
            "error": str(exc),
            "latency_ms": latency_ms,
            "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None},
            "estimated_cost_usd": None,
            "cost_reason": "error",
            "generated_title": None,
            "title_chars": 0,
            "request_chars": request_chars,
            "request_tokens_estimate": request_tokens_estimate,
            "request_tokens_actual": None,
            "output_chars": 0,
            "display_output": None,
            "raw_output": None,
            "prompt_type": prompt_type,
        }


def _extract_result_payload(result: Any) -> dict[str, Any]:
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


def _resolve_prompt_settings(
    content_type: EvalContentType,
    request: AdminEvalRunRequest,
) -> tuple[str, int, int]:
    if content_type == "news":
        return "news_digest", 4, 0

    if request.longform_template == "interleaved_v2":
        return "interleaved", 8, 8
    if request.longform_template == "structured_v1":
        return "structured", 12, 8
    if request.longform_template == "editorial_narrative_v1":
        return "editorial_narrative", 10, 4
    return "long_bullets", 30, 3


def _extract_input_text(content_type: str, metadata: dict[str, Any]) -> str | None:
    if content_type == "article":
        text = metadata.get("content") or metadata.get("content_to_summarize")
        return text if isinstance(text, str) and text.strip() else None

    if content_type == "podcast":
        text = metadata.get("transcript") or metadata.get("content_to_summarize")
        return text if isinstance(text, str) and text.strip() else None

    if content_type == "news":
        text = metadata.get("content") or metadata.get("content_to_summarize")
        if not isinstance(text, str) or not text.strip():
            return None

        context = _build_news_context(metadata)
        if context:
            return f"Context:\n{context}\n\nArticle Content:\n{text}"
        return text

    return None


def _clip_eval_input(text: str) -> str:
    """Clip long eval inputs to keep requests bounded and responsive."""
    if len(text) <= MAX_EVAL_INPUT_CHARS:
        return text

    marker = "\n\n[... CONTENT TRUNCATED FOR EVAL ...]\n\n"
    remaining = MAX_EVAL_INPUT_CHARS - len(marker)
    if remaining <= 0:
        return text[:MAX_EVAL_INPUT_CHARS]

    head_size = remaining // 2
    tail_size = remaining - head_size
    return f"{text[:head_size].rstrip()}{marker}{text[-tail_size:].lstrip()}"


def _should_disable_model_after_error(error_text: str) -> bool:
    """Disable a model for the current run after hard API/provider errors."""
    lowered = error_text.lower()
    fatal_markers = (
        "status_code: 400",
        "status_code: 401",
        "status_code: 403",
        "status_code: 404",
        "timed out",
        "timeout",
        "not found",
        "model_not_found",
        "invalid argument",
        "permission",
        "authentication",
    )
    return any(marker in lowered for marker in fatal_markers)


def _build_news_context(metadata: dict[str, Any]) -> str:
    """Build aggregator context string for news items."""
    article = metadata.get("article", {})
    aggregator = metadata.get("aggregator", {})
    lines: list[str] = []

    article_title = article.get("title") or ""
    article_url = article.get("url") or ""

    if article_title:
        lines.append(f"Article Title: {article_title}")
    if article_url:
        lines.append(f"Article URL: {article_url}")

    if aggregator:
        name = aggregator.get("name") or metadata.get("platform")
        agg_title = aggregator.get("title")
        agg_url = metadata.get("discussion_url") or aggregator.get("url")
        author = aggregator.get("author")

        context_bits = []
        if name:
            context_bits.append(name)
        if author:
            context_bits.append(f"by {author}")
        if agg_title and agg_title != article_title:
            lines.append(f"Aggregator Headline: {agg_title}")
        if context_bits:
            lines.append("Aggregator Context: " + ", ".join(context_bits))
        if agg_url:
            lines.append(f"Discussion URL: {agg_url}")

        extra = aggregator.get("metadata") or {}
        highlights = []
        for field in ["score", "comments_count", "likes", "retweets", "replies"]:
            value = extra.get(field)
            if value is not None:
                highlights.append(f"{field}={value}")
        if highlights:
            lines.append("Signals: " + ", ".join(highlights))

    summary_payload = metadata.get("summary") if isinstance(metadata, dict) else {}
    excerpt = metadata.get("excerpt")
    if not excerpt and isinstance(summary_payload, dict):
        excerpt = (
            summary_payload.get("overview")
            or summary_payload.get("summary")
            or summary_payload.get("hook")
            or summary_payload.get("takeaway")
        )
    if excerpt:
        lines.append(f"Aggregator Summary: {excerpt}")

    return "\n".join(lines)


def _resolve_model_availability(
    models: list[str],
) -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
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


def _extract_usage(result: Any) -> dict[str, int | None]:
    try:
        usage = result.usage()
    except Exception:  # noqa: BLE001
        usage = None

    if not usage:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None}

    input_tokens = _coerce_int(
        getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
    )
    output_tokens = _coerce_int(
        getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None)
    )
    total_tokens = _coerce_int(getattr(usage, "total_tokens", None))

    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _estimate_cost(
    usage: dict[str, int | None],
    pricing: ModelPricing | None,
) -> tuple[float | None, str | None]:
    if pricing is None:
        return None, "pricing_not_configured"

    if pricing.input_per_million_usd is None or pricing.output_per_million_usd is None:
        return None, "pricing_not_configured"

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if input_tokens is None or output_tokens is None:
        return None, "usage_not_available"

    cost = (
        (input_tokens / 1_000_000) * pricing.input_per_million_usd
        + (output_tokens / 1_000_000) * pricing.output_per_million_usd
    )
    return round(cost, 8), None


def _coerce_int(value: object | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _estimate_tokens_from_chars(char_count: int) -> int:
    if char_count <= 0:
        return 0
    return math.ceil(char_count / ESTIMATED_CHARS_PER_TOKEN)


def _build_aggregate_metrics(item_results: list[dict[str, Any]]) -> dict[str, Any]:
    cells = [
        model_result
        for item in item_results
        for model_result in item.get("model_results", [])
    ]
    successful = [cell for cell in cells if cell.get("status") == "ok"]

    total_estimated_cost_usd = round(
        sum(cell.get("estimated_cost_usd") or 0 for cell in successful),
        8,
    )

    def _avg(values: list[int]) -> float | None:
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    latency_values = [
        int(cell["latency_ms"]) for cell in successful if cell.get("latency_ms") is not None
    ]
    input_token_values = [
        int(cell["usage"]["input_tokens"])
        for cell in successful
        if cell.get("usage", {}).get("input_tokens") is not None
    ]
    output_token_values = [
        int(cell["usage"]["output_tokens"])
        for cell in successful
        if cell.get("usage", {}).get("output_tokens") is not None
    ]
    output_char_values = [
        int(cell["output_chars"]) for cell in successful if cell.get("output_chars") is not None
    ]
    request_char_values = [
        int(cell["request_chars"]) for cell in successful if cell.get("request_chars") is not None
    ]
    request_token_estimate_values = [
        int(cell["request_tokens_estimate"])
        for cell in successful
        if cell.get("request_tokens_estimate") is not None
    ]
    request_token_actual_values = [
        int(cell["request_tokens_actual"])
        for cell in successful
        if cell.get("request_tokens_actual") is not None
    ]

    return {
        "items_total": len(item_results),
        "cells_total": len(cells),
        "cells_successful": len(successful),
        "cells_failed": len(cells) - len(successful),
        "avg_latency_ms": _avg(latency_values),
        "avg_input_tokens": _avg(input_token_values),
        "avg_output_tokens": _avg(output_token_values),
        "avg_output_chars": _avg(output_char_values),
        "avg_request_chars": _avg(request_char_values),
        "avg_request_tokens_estimate": _avg(request_token_estimate_values),
        "avg_request_tokens_actual": _avg(request_token_actual_values),
        "total_estimated_cost_usd": total_estimated_cost_usd,
    }
