"""Shared persistence and pricing helpers for vendor usage telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.core.settings import get_settings
from app.models.schema import VendorUsageRecord
from app.services.llm_models import resolve_model_provider

logger = get_logger("vendor.cost")

PRICING_VERSION = "2026-04-20"
USD = "USD"


@dataclass(frozen=True)
class ModelPricing:
    input_per_million_usd: float | None
    output_per_million_usd: float | None
    long_context_threshold_tokens: int | None = None
    long_context_input_per_million_usd: float | None = None
    long_context_output_per_million_usd: float | None = None


@dataclass(frozen=True)
class UnitPricing:
    request_usd: float | None = None
    resource_usd: float | None = None


# Standard online pricing for the exact model names we resolve in production.
# For preview snapshots vendors no longer list directly, we pin the nearest current
# official pricing for the same model family and keep the alias explicit below.
MODEL_PRICING: dict[str, ModelPricing] = {
    # OpenAI
    "gpt-5.4": ModelPricing(
        input_per_million_usd=2.50,
        output_per_million_usd=15.00,
        long_context_threshold_tokens=272_000,
        long_context_input_per_million_usd=5.00,
        long_context_output_per_million_usd=22.50,
    ),
    "gpt-5.4-mini": ModelPricing(
        input_per_million_usd=0.75,
        output_per_million_usd=4.50,
    ),
    "gpt-4o": ModelPricing(
        input_per_million_usd=2.50,
        output_per_million_usd=10.00,
    ),
    "o4-mini-deep-research": ModelPricing(
        input_per_million_usd=2.00,
        output_per_million_usd=8.00,
    ),
    # Anthropic
    "claude-opus-4-5-20251101": ModelPricing(
        input_per_million_usd=5.00,
        output_per_million_usd=25.00,
    ),
    "claude-sonnet-4-5-20250929": ModelPricing(
        input_per_million_usd=3.00,
        output_per_million_usd=15.00,
    ),
    # Google
    "gemini-3.1-pro-preview": ModelPricing(
        input_per_million_usd=2.00,
        output_per_million_usd=12.00,
        long_context_threshold_tokens=200_000,
        long_context_input_per_million_usd=4.00,
        long_context_output_per_million_usd=18.00,
    ),
    "gemini-3.1-flash-lite-preview": ModelPricing(
        input_per_million_usd=0.25,
        output_per_million_usd=1.50,
    ),
    "gemini-3-flash-preview": ModelPricing(
        input_per_million_usd=0.50,
        output_per_million_usd=3.00,
    ),
    # Image generation output for Gemini image-preview models is token-priced by Google.
    "gemini-3.1-flash-image-preview": ModelPricing(
        input_per_million_usd=0.50,
        output_per_million_usd=60.00,
    ),
}


# Older snapshots and repo-specific aliases that should inherit canonical pricing.
MODEL_ALIASES: dict[str, str] = {
    "claude-opus-4-5": "claude-opus-4-5-20251101",
    "claude-sonnet-4-5": "claude-sonnet-4-5-20250929",
    "gemini-3-pro-preview": "gemini-3.1-pro-preview",
    "o4-mini-deep-research-2025-06-26": "o4-mini-deep-research",
}


def extract_usage_from_result(result: object) -> dict[str, int | None] | None:
    """Extract token usage from a pydantic-ai style result object."""
    usage_fn = getattr(result, "usage", None)
    if not callable(usage_fn):
        return None
    try:
        usage = usage_fn()
    except Exception:  # noqa: BLE001
        return None

    if not usage:
        return None

    input_tokens = _coerce_int(
        getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
    )
    output_tokens = _coerce_int(
        getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None)
    )
    total_tokens = _coerce_int(getattr(usage, "total_tokens", None))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def record_vendor_usage(
    db: Session,
    *,
    provider: str | None,
    model: str,
    feature: str,
    operation: str,
    source: str | None = None,
    usage: dict[str, int | None] | None,
    request_id: str | None = None,
    task_id: int | None = None,
    content_id: int | None = None,
    session_id: int | None = None,
    message_id: int | None = None,
    user_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> VendorUsageRecord | None:
    """Persist one vendor usage record and emit a structured log."""
    normalized_usage = _normalize_usage(usage)
    if normalized_usage is None:
        return None

    provider_name = provider or resolve_model_provider(model)
    cost_usd = estimate_vendor_cost_usd(
        provider=provider_name,
        model=model,
        usage=normalized_usage,
        metadata=metadata,
    )
    record = VendorUsageRecord(
        provider=provider_name,
        model=model,
        feature=feature,
        operation=operation,
        source=source,
        request_id=request_id,
        task_id=task_id,
        content_id=content_id,
        session_id=session_id,
        message_id=message_id,
        user_id=user_id,
        input_tokens=normalized_usage.get("input_tokens"),
        output_tokens=normalized_usage.get("output_tokens"),
        total_tokens=normalized_usage.get("total_tokens"),
        request_count=normalized_usage.get("request_count"),
        resource_count=normalized_usage.get("resource_count"),
        cost_usd=cast(Any, cost_usd),
        currency=USD,
        pricing_version=PRICING_VERSION,
        metadata_json=metadata or {},
    )
    db.add(record)
    try:
        db.flush()
    except SQLAlchemyError:
        # Usage tracking must never poison the caller's session.
        db.rollback()
        logger.warning(
            "Failed to persist vendor usage record; continuing without telemetry",
            extra=build_log_extra(
                component="vendor_costs",
                operation=operation,
                event_name="vendor.usage",
                status="degraded",
                request_id=request_id,
                task_id=task_id,
                content_id=content_id,
                session_id=session_id,
                message_id=message_id,
                user_id=user_id,
                provider=provider_name,
                model=model,
                source=source,
                context_data={
                    "feature": feature,
                    "pricing_version": PRICING_VERSION,
                },
            ),
        )
        return None

    logger.info(
        "Recorded vendor usage",
        extra=build_log_extra(
            component="vendor_costs",
            operation=operation,
            event_name="vendor.usage",
            status="completed",
            request_id=request_id,
            task_id=task_id,
            content_id=content_id,
            session_id=session_id,
            message_id=message_id,
            user_id=user_id,
            provider=provider_name,
            model=model,
            source=source,
            context_data={
                "feature": feature,
                "input_tokens": normalized_usage.get("input_tokens"),
                "output_tokens": normalized_usage.get("output_tokens"),
                "total_tokens": normalized_usage.get("total_tokens"),
                "request_count": normalized_usage.get("request_count"),
                "resource_count": normalized_usage.get("resource_count"),
                "cost_usd": cost_usd,
                "pricing_version": PRICING_VERSION,
            },
        ),
    )

    return record


def record_vendor_usage_out_of_band(
    *,
    provider: str | None,
    model: str,
    feature: str,
    operation: str,
    source: str | None = None,
    usage: dict[str, int | None] | None,
    request_id: str | None = None,
    task_id: int | None = None,
    content_id: int | None = None,
    session_id: int | None = None,
    message_id: int | None = None,
    user_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> VendorUsageRecord | None:
    """Persist one vendor usage record using a dedicated short-lived session."""
    if usage is None:
        return None

    try:
        with get_db() as db:
            return record_vendor_usage(
                db,
                provider=provider,
                model=model,
                feature=feature,
                operation=operation,
                source=source,
                usage=usage,
                request_id=request_id,
                task_id=task_id,
                content_id=content_id,
                session_id=session_id,
                message_id=message_id,
                user_id=user_id,
                metadata=metadata,
            )
    except SQLAlchemyError:
        logger.warning(
            "Failed to persist out-of-band vendor usage record; continuing without telemetry",
            extra=build_log_extra(
                component="vendor_costs",
                operation=operation,
                event_name="vendor.usage",
                status="degraded",
                request_id=request_id,
                task_id=task_id,
                content_id=content_id,
                session_id=session_id,
                message_id=message_id,
                user_id=user_id,
                provider=provider or resolve_model_provider(model),
                model=model,
                source=source,
                context_data={
                    "feature": feature,
                    "pricing_version": PRICING_VERSION,
                },
            ),
        )
        return None


def estimate_vendor_cost_usd(
    *,
    provider: str,
    model: str,
    usage: dict[str, int | None],
    metadata: dict[str, Any] | None = None,
) -> float | None:
    """Estimate USD cost from token or unit pricing registries."""
    normalized_usage = _normalize_usage(usage)
    if normalized_usage is None:
        return None

    token_cost = _estimate_token_cost_usd(
        provider=provider,
        model=model,
        input_tokens=normalized_usage.get("input_tokens"),
        output_tokens=normalized_usage.get("output_tokens"),
    )
    unit_cost = _estimate_unit_cost_usd(
        provider=provider,
        model=model,
        request_count=normalized_usage.get("request_count"),
        resource_count=normalized_usage.get("resource_count"),
        metadata=metadata,
    )

    contributions = [value for value in (token_cost, unit_cost) if value is not None]
    if not contributions:
        return None
    return round(sum(contributions), 8)


def _estimate_token_cost_usd(
    *,
    provider: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> float | None:
    pricing = _resolve_model_pricing(provider=provider, model=model)
    if pricing is None:
        return None
    if input_tokens is None or output_tokens is None:
        return None

    input_rate = pricing.input_per_million_usd
    output_rate = pricing.output_per_million_usd
    if (
        pricing.long_context_threshold_tokens is not None
        and input_tokens > pricing.long_context_threshold_tokens
    ):
        input_rate = pricing.long_context_input_per_million_usd or input_rate
        output_rate = pricing.long_context_output_per_million_usd or output_rate

    if input_rate is None or output_rate is None:
        return None

    cost = (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate
    return round(cost, 8)


def _estimate_unit_cost_usd(
    *,
    provider: str,
    model: str,
    request_count: int | None,
    resource_count: int | None,
    metadata: dict[str, Any] | None = None,
) -> float | None:
    exa_cost = _estimate_exa_unit_cost_usd(
        provider=provider,
        model=model,
        request_count=request_count,
        resource_count=resource_count,
        metadata=metadata,
    )
    if exa_cost is not None:
        return exa_cost

    pricing = _resolve_unit_pricing(provider=provider, model=model)
    if pricing is None:
        return None

    total = 0.0
    has_cost = False
    if request_count is not None and pricing.request_usd is not None:
        total += request_count * pricing.request_usd
        has_cost = True
    if resource_count is not None and pricing.resource_usd is not None:
        total += resource_count * pricing.resource_usd
        has_cost = True

    if not has_cost:
        return None
    return round(total, 8)


def _estimate_exa_unit_cost_usd(
    *,
    provider: str,
    model: str,
    request_count: int | None,
    resource_count: int | None,
    metadata: dict[str, Any] | None,
) -> float | None:
    if provider != "exa":
        return None

    settings = get_settings()
    normalized_metadata = metadata if isinstance(metadata, dict) else {}
    requests = request_count if request_count is not None and request_count > 0 else 1

    if model == "search":
        requested_results = _coerce_int(normalized_metadata.get("requested_num_results"))
        if requested_results is None:
            requested_results = resource_count or 0

        base_cost = requests * (settings.exa_search_request_cost_usd or 0.0)
        included_results = max(settings.exa_search_included_results, 0)
        additional_results = max(requested_results - included_results, 0)
        additional_result_cost = (
            requests * additional_results * (settings.exa_content_result_cost_usd or 0.0)
        )

        includes_summary = normalized_metadata.get("includes_summary")
        if includes_summary is None:
            includes_summary = True
        summary_result_count = requested_results if includes_summary else 0
        summary_cost = (
            requests * summary_result_count * (settings.exa_summary_result_cost_usd or 0.0)
        )
        return round(base_cost + additional_result_cost + summary_cost, 8)

    if model == "contents":
        requested_pages = _coerce_int(normalized_metadata.get("url_count"))
        if requested_pages is None:
            requested_pages = resource_count
        if requested_pages is None:
            return None

        requested_content_types = normalized_metadata.get("content_types_requested")
        content_type_count = (
            len(requested_content_types) if isinstance(requested_content_types, list) else 1
        )
        if content_type_count <= 0:
            content_type_count = 1

        total = (
            requests
            * requested_pages
            * content_type_count
            * (settings.exa_content_result_cost_usd or 0.0)
        )
        return round(total, 8)

    return None


def _resolve_model_pricing(*, provider: str, model: str) -> ModelPricing | None:
    for candidate in _pricing_candidates(provider=provider, model=model):
        if candidate in MODEL_PRICING:
            return MODEL_PRICING[candidate]
    return MODEL_PRICING.get(provider)


def _resolve_unit_pricing(*, provider: str, model: str) -> UnitPricing | None:
    settings = get_settings()
    unit_pricing: dict[str, UnitPricing] = {
        "x:posts.read": UnitPricing(resource_usd=settings.x_posts_read_cost_usd),
        "x:users.read": UnitPricing(resource_usd=settings.x_users_read_cost_usd),
        "runware:runware:101@1": UnitPricing(request_usd=0.0038),
    }
    for candidate in _pricing_candidates(provider=provider, model=model):
        if candidate in unit_pricing:
            return unit_pricing[candidate]
    return unit_pricing.get(provider)


def _normalize_usage(usage: dict[str, int | None] | None) -> dict[str, int | None] | None:
    if usage is None:
        return None

    input_tokens = _coerce_int(usage.get("input_tokens", usage.get("input")))
    output_tokens = _coerce_int(usage.get("output_tokens", usage.get("output")))
    total_tokens = _coerce_int(usage.get("total_tokens", usage.get("total")))
    request_count = _coerce_int(usage.get("request_count", usage.get("requests")))
    resource_count = _coerce_int(usage.get("resource_count", usage.get("resources")))

    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    if (
        input_tokens is None
        and output_tokens is None
        and total_tokens is None
        and request_count is None
        and resource_count is None
    ):
        return None

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "request_count": request_count,
        "resource_count": resource_count,
    }


def _pricing_candidates(*, provider: str, model: str) -> list[str]:
    candidates: list[str] = []

    def _add(value: str | None) -> None:
        if value and value not in candidates:
            candidates.append(value)

    _add(model)
    model_name = model.split(":", 1)[1] if ":" in model else model
    _add(model_name)

    for candidate in list(candidates):
        _add(MODEL_ALIASES.get(candidate))

    _add(f"{provider}:{model}")
    if model_name:
        _add(f"{provider}:{model_name}")
        aliased_name = MODEL_ALIASES.get(model_name)
        if aliased_name:
            _add(aliased_name)
            _add(f"{provider}:{aliased_name}")

    return candidates


def _coerce_int(value: object | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
