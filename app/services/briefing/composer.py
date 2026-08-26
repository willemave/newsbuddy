from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError
from pydantic_ai.agent import AgentRunResult

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.services.briefing.event_grouping import group_news_events
from app.services.briefing.layout_models import ComposerLayout
from app.services.briefing.layout_policy import (
    BriefingLayoutAssessment,
    BriefingLayoutDisposition,
    assess_briefing_layout,
    is_low_signal_generated_text,
)
from app.services.briefing.normalize import NormalizedLayout, normalize_layout
from app.services.briefing.openrouter import (
    StructuredOutputRequester,
    request_openrouter_json_schema,
    strip_json_code_fence,
)
from app.services.briefing.repair import repair_layout
from app.services.briefing.sources import BriefingSource
from app.services.llm_agents import get_basic_agent
from app.services.prompt_library import render_prompt
from app.services.vendor_costs import extract_usage_from_result, record_vendor_usage_out_of_band
from app.services.vendor_usage import record_model_usage

PROMPT_VERSION = "briefing-v6"
MAX_COMPOSE_ATTEMPTS = 4
LAYOUT_PROMPTS_BY_TIER = {
    "audio": "briefing/layout_audio",
    "longform": "briefing/layout_longform",
    "news": "briefing/layout_news",
}
logger = get_logger(__name__)


class BriefingCompositionError(RuntimeError):
    """Base error for briefing composition failures that should fail the task."""


class BriefingCompositionInvalidOutput(BriefingCompositionError):
    """The LLM returned output that cannot produce a valid briefing segment."""


class LayoutGenerator(Protocol):
    def __call__(
        self,
        sources: list[BriefingSource],
        *,
        lens_title: str,
        tier: str,
        model_spec: str,
        timeout_seconds: int,
        task_id: int | None,
        user_id: int | None,
    ) -> tuple[list[dict[str, Any]], dict[str, int | None] | None]: ...


@dataclass(frozen=True)
class ComposedSegment:
    blocks: list[dict[str, Any]]
    markdown_raw: str
    narration_text: str
    status: str
    model: str
    prompt_version: str
    input_tokens: int | None
    output_tokens: int | None
    generation_ms: int
    warnings: list[str]
    raw_blocks: list[dict[str, Any]] | None = None
    raw_assessment: BriefingLayoutAssessment | None = None
    final_assessment: BriefingLayoutAssessment | None = None
    generation_attempts: int = 0


@dataclass(frozen=True)
class ProcessedLayout:
    raw_blocks: list[dict[str, Any]]
    raw_assessment: BriefingLayoutAssessment
    normalized: NormalizedLayout | None
    final_assessment: BriefingLayoutAssessment | None
    warnings: list[str]

    @property
    def accepted(self) -> bool:
        return (
            self.normalized is not None
            and self.final_assessment is not None
            and self.final_assessment.disposition == BriefingLayoutDisposition.ACCEPT
        )


def plan_event_windows[WindowItem](
    sources: list[WindowItem],
    *,
    tier: str,
    settings: Settings | None = None,
    source_of: Callable[[WindowItem], BriefingSource] | None = None,
) -> list[list[list[WindowItem]]]:
    """Split sources into composition windows of events.

    News windows hold up to ``briefing_news_window_max`` *events*, not rows:
    when ``source_of`` is given, sources covering one event are grouped first
    and always land in the same window, however many of them there are.
    Other tiers yield one single-source event per window.
    """
    if tier != "news":
        return [[[source]] for source in sources]

    settings = settings or get_settings()
    max_size = max(1, settings.briefing_news_window_max)
    if not sources:
        return []
    events: list[list[WindowItem]]
    if source_of is None:
        events = [[source] for source in sources]
    else:
        events = group_news_events(sources, source_of=source_of, settings=settings)
    window_count = (len(events) + max_size - 1) // max_size
    base_size, larger_windows = divmod(len(events), window_count)
    windows: list[list[list[WindowItem]]] = []
    start = 0
    for window_index in range(window_count):
        size = base_size + int(window_index < larger_windows)
        windows.append(events[start : start + size])
        start += size
    return windows


def plan_windows[WindowItem](
    sources: list[WindowItem],
    *,
    tier: str,
    settings: Settings | None = None,
    source_of: Callable[[WindowItem], BriefingSource] | None = None,
) -> list[list[WindowItem]]:
    """Flattened ``plan_event_windows``: each window as its ordered sources."""
    return [
        [item for event in window for item in event]
        for window in plan_event_windows(sources, tier=tier, settings=settings, source_of=source_of)
    ]


def compose_window(
    sources: list[BriefingSource],
    *,
    lens_key: str,
    lens_title: str,
    tier: str,
    window_index: int,
    task_id: int | None = None,
    user_id: int | None = None,
    settings: Settings | None = None,
    layout_generator: LayoutGenerator | None = None,
) -> ComposedSegment:
    settings = settings or get_settings()
    layout_generator = layout_generator or generate_layout_with_llm
    started_at = time.perf_counter()
    model_spec = settings.briefing_model
    warnings: list[str] = []
    input_tokens: int | None = None
    output_tokens: int | None = None
    processed: ProcessedLayout | None = None
    generation_attempts = 0
    figure_budget = (
        settings.briefing_max_figures_news if tier == "news" else settings.briefing_max_figures_deep
    )

    for attempt in range(1, MAX_COMPOSE_ATTEMPTS + 1):
        generation_attempts = attempt
        try:
            llm_blocks, usage = layout_generator(
                sources,
                lens_title=lens_title,
                tier=tier,
                model_spec=model_spec,
                timeout_seconds=settings.briefing_llm_timeout_seconds,
                task_id=task_id,
                user_id=user_id,
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning(
                "Briefing LLM returned invalid layout JSON",
                extra={
                    "component": "briefing",
                    "operation": "compose_window",
                    "task_id": task_id,
                    "item_id": user_id,
                    "context_data": {
                        "lens_key": lens_key,
                        "tier": tier,
                        "window_index": window_index,
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                },
            )
            if attempt >= MAX_COMPOSE_ATTEMPTS:
                raise BriefingCompositionInvalidOutput(
                    "Briefing LLM returned invalid layout JSON after retries"
                ) from exc
            warnings.append(f"llm_invalid_layout_retry:{attempt}")
            continue
        except BriefingCompositionInvalidOutput as exc:
            logger.warning(
                "Briefing LLM returned invalid layout output",
                extra={
                    "component": "briefing",
                    "operation": "compose_window",
                    "task_id": task_id,
                    "item_id": user_id,
                    "context_data": {
                        "lens_key": lens_key,
                        "tier": tier,
                        "window_index": window_index,
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                },
            )
            if attempt >= MAX_COMPOSE_ATTEMPTS:
                raise
            warnings.append(f"llm_invalid_output_retry:{attempt}")
            continue
        except Exception as exc:
            logger.warning(
                "Briefing LLM composition failed; retrying",
                extra={
                    "component": "briefing",
                    "operation": "compose_window",
                    "task_id": task_id,
                    "item_id": user_id,
                    "context_data": {
                        "lens_key": lens_key,
                        "tier": tier,
                        "window_index": window_index,
                        "source_count": len(sources),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                },
                exc_info=True,
            )
            if attempt >= MAX_COMPOSE_ATTEMPTS:
                raise BriefingCompositionError(
                    "Briefing LLM composition failed after retries"
                ) from exc
            warnings.append(f"llm_error_retry:{attempt}")
            continue
        candidate = process_generated_layout(
            llm_blocks,
            sources=sources,
            figure_budget=figure_budget,
            ensure_source_figures=tier != "news",
        )
        news_contract_issues = (
            news_layout_contract_issues(candidate, sources=sources) if tier == "news" else []
        )
        if candidate.accepted and not news_contract_issues:
            processed = candidate
            if usage:
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
            break
        logger.warning(
            "Briefing LLM layout policy requested a fresh generation",
            extra={
                "component": "briefing",
                "operation": "compose_window",
                "task_id": task_id,
                "item_id": user_id,
                "context_data": {
                    "lens_key": lens_key,
                    "tier": tier,
                    "window_index": window_index,
                    "attempt": attempt,
                    "raw_disposition": candidate.raw_assessment.disposition.value,
                    "raw_issues": candidate.raw_assessment.issues,
                    "final_issues": (
                        candidate.final_assessment.issues
                        if candidate.final_assessment is not None
                        else []
                    ),
                    "news_contract_issues": news_contract_issues,
                },
            },
        )
        if attempt >= MAX_COMPOSE_ATTEMPTS:
            raise BriefingCompositionInvalidOutput(
                "Briefing LLM layout failed policy after retries"
            )
        warnings.append(f"llm_layout_policy_retry:{attempt}")
    if processed is None or not processed.accepted or processed.normalized is None:
        raise BriefingCompositionInvalidOutput(
            "Briefing composition did not produce a policy-valid normalized layout"
        )

    normalized = processed.normalized
    warnings.extend(processed.warnings)
    generation_ms = round((time.perf_counter() - started_at) * 1000)
    return ComposedSegment(
        blocks=normalized.blocks,
        markdown_raw=normalized.markdown_raw,
        narration_text=normalized.narration_text,
        status="active",
        model=model_spec,
        prompt_version=PROMPT_VERSION,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        generation_ms=generation_ms,
        warnings=warnings,
        raw_blocks=processed.raw_blocks,
        raw_assessment=processed.raw_assessment,
        final_assessment=processed.final_assessment,
        generation_attempts=generation_attempts,
    )


def process_generated_layout(
    blocks: list[dict[str, Any]],
    *,
    sources: list[BriefingSource],
    figure_budget: int,
    ensure_source_figures: bool,
) -> ProcessedLayout:
    """Run the same assessment, repair, normalization, and reassessment used in production."""
    raw_blocks = [dict(block) for block in blocks]
    source_keys = {source.source_key for source in sources}
    source_keys_with_images = {
        source.source_key for source in sources if source.image_url or source.thumbnail_url
    }
    raw_assessment = assess_briefing_layout(
        raw_blocks,
        source_keys=source_keys,
        source_keys_with_images=source_keys_with_images,
        figure_budget=figure_budget,
    )
    if raw_assessment.disposition == BriefingLayoutDisposition.RETRY:
        return ProcessedLayout(
            raw_blocks=raw_blocks,
            raw_assessment=raw_assessment,
            normalized=None,
            final_assessment=None,
            warnings=[],
        )

    repaired = repair_layout(
        raw_blocks,
        sources=sources,
        figure_budget=figure_budget,
        ensure_source_figures=ensure_source_figures,
        assessment=raw_assessment,
    )
    normalized = normalize_layout(repaired.blocks, source_keys=source_keys)
    final_assessment = assess_briefing_layout(
        normalized.blocks,
        source_keys=source_keys,
        source_keys_with_images=source_keys_with_images,
        figure_budget=figure_budget,
    )
    warnings = list(repaired.warnings)
    if raw_assessment.disposition == BriefingLayoutDisposition.REPAIR:
        warnings.insert(0, "layout_policy_repair")
    warnings.extend(normalized.warnings)
    return ProcessedLayout(
        raw_blocks=raw_blocks,
        raw_assessment=raw_assessment,
        normalized=normalized,
        final_assessment=final_assessment,
        warnings=warnings,
    )


def news_layout_contract_issues(
    processed: ProcessedLayout,
    *,
    sources: list[BriefingSource],
) -> list[str]:
    if len(processed.raw_blocks) != 1 or processed.raw_blocks[0].get("type") != "passage":
        return ["news_requires_one_passage"]
    raw_markdown = str(
        processed.raw_blocks[0].get("markdown") or processed.raw_blocks[0].get("text") or ""
    ).strip()
    raw_paragraphs = [
        paragraph for paragraph in re.split(r"\n\s*\n", raw_markdown) if paragraph.strip()
    ]
    if len(raw_paragraphs) != 1:
        return ["news_requires_one_paragraph"]

    normalized = processed.normalized
    if normalized is None:
        return ["missing_normalized_layout"]
    if len(normalized.blocks) != 1 or normalized.blocks[0].get("type") != "passage":
        return ["news_requires_one_passage"]

    paragraphs = normalized.blocks[0].get("paragraphs")
    if not isinstance(paragraphs, list) or len(paragraphs) != 1:
        return ["news_requires_one_paragraph"]

    linked_keys = [
        str(run.get("source_key"))
        for run in paragraphs[0].get("runs", [])
        if isinstance(run, dict)
        and run.get("kind") == "source_link"
        and run.get("source_key") is not None
    ]
    if Counter(linked_keys) != Counter(source.source_key for source in sources):
        return ["news_requires_each_source_linked_once"]
    return []


def generate_layout_with_llm(
    sources: list[BriefingSource],
    *,
    lens_title: str,
    tier: str,
    model_spec: str,
    timeout_seconds: int,
    task_id: int | None,
    user_id: int | None,
    structured_output_requester: StructuredOutputRequester | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int | None] | None]:
    prompt_name = _layout_prompt_name(tier)
    system_prompt = render_prompt(f"{prompt_name}#system")
    user_prompt = render_prompt(
        f"{prompt_name}#window",
        lens_title=lens_title,
        source_payload_json=json.dumps(
            [_source_payload(source) for source in sources],
            ensure_ascii=False,
            indent=2,
        ),
    )

    if model_spec.startswith("openrouter:"):
        return _compose_window_with_openrouter(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model_spec=model_spec,
            timeout_seconds=timeout_seconds,
            task_id=task_id,
            user_id=user_id,
            structured_output_requester=structured_output_requester,
        )

    result = _run_agent(model_spec, system_prompt, user_prompt, timeout_seconds=timeout_seconds)
    record_model_usage(
        "briefing_compose",
        result,
        model_spec=model_spec,
        persist={
            "feature": "briefing_compose",
            "operation": "briefing.compose_window",
            "source": "queue" if task_id else "api",
            "task_id": task_id,
            "user_id": user_id,
        },
    )
    usage = extract_usage_from_result(result)
    return (
        result.output.resolved_blocks(),
        usage,
    )


def _compose_window_with_openrouter(
    *,
    system_prompt: str,
    user_prompt: str,
    model_spec: str,
    timeout_seconds: int,
    task_id: int | None,
    user_id: int | None,
    structured_output_requester: StructuredOutputRequester | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int | None] | None]:
    try:
        response = request_openrouter_json_schema(
            model_spec=model_spec,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name="ComposerLayout",
            schema=ComposerLayout.model_json_schema(),
            timeout_seconds=timeout_seconds,
            requester=structured_output_requester,
        )
    except RuntimeError as exc:
        raise BriefingCompositionInvalidOutput(str(exc)) from exc
    layout = _parse_composer_layout_json(response.content)
    usage = response.usage
    record_vendor_usage_out_of_band(
        provider="openrouter",
        model=model_spec,
        feature="briefing_compose",
        operation="briefing.compose_window",
        source="queue" if task_id else "api",
        usage=usage,
        task_id=task_id,
        user_id=user_id,
    )
    return (
        layout.resolved_blocks(),
        usage,
    )


def _parse_composer_layout_json(content: str) -> ComposerLayout:
    payload = json.loads(strip_json_code_fence(content))
    if isinstance(payload, list):
        return ComposerLayout(blocks=[_coerce_composer_block(block) for block in payload])
    if isinstance(payload, dict):
        blocks = payload.get("blocks")
        if blocks is None:
            blocks = payload.get("layout")
        if isinstance(blocks, list):
            payload = {
                **payload,
                "blocks": [_coerce_composer_block(block) for block in blocks],
            }
            payload.pop("layout", None)
    return ComposerLayout.model_validate(payload)


def _coerce_composer_block(block: Any) -> Any:
    if not isinstance(block, dict):
        return block
    coerced = dict(block)
    content = coerced.pop("content", None)
    _recover_weight_payload(coerced)
    block_type = str(coerced.get("type") or "").strip().lower()
    if isinstance(content, str) and content.strip():
        if block_type == "figure" and not coerced.get("caption"):
            coerced["caption"] = content
        elif not coerced.get("markdown"):
            coerced["markdown"] = content
    if block_type == "passage" and not coerced.get("markdown"):
        legacy_text = coerced.pop("text", None)
        if isinstance(legacy_text, str) and legacy_text.strip():
            coerced["markdown"] = legacy_text
    return coerced


def _recover_weight_payload(block: dict[str, Any]) -> None:
    raw_weight = block.get("weight")
    if not isinstance(raw_weight, str) or not raw_weight.strip():
        return
    if raw_weight.strip().lower() in {"feature", "brief"}:
        return
    if _has_block_content(block):
        block.pop("weight", None)
        return
    if is_low_signal_generated_text(raw_weight, allow_source_links=False):
        block.pop("weight", None)
        return

    block_type = str(block.get("type") or "").strip().lower()
    if block_type == "figure":
        block["caption"] = raw_weight.strip()
    else:
        block["markdown"] = raw_weight.strip()
    block.pop("weight", None)


def _has_block_content(block: dict[str, Any]) -> bool:
    for key in ("markdown", "text", "source_key", "caption"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _run_agent(
    model_spec: str,
    system_prompt: str,
    user_prompt: str,
    *,
    timeout_seconds: int,
) -> AgentRunResult[ComposerLayout]:
    agent = get_basic_agent(model_spec, ComposerLayout, system_prompt)
    return agent.run_sync(user_prompt, model_settings={"timeout": timeout_seconds})


def _layout_prompt_name(tier: str) -> str:
    prompt_name = LAYOUT_PROMPTS_BY_TIER.get(tier)
    if prompt_name is None:
        raise ValueError(f"No briefing layout prompt for tier: {tier}")
    return prompt_name


def _source_payload(source: BriefingSource) -> dict[str, Any]:
    payload = {
        "source_key": source.source_key,
        "kind": source.kind,
        "id": source.id,
        "title": source.title,
        "source_name": source.source_name,
        "summary": source.summary,
        "key_points": source.key_points,
        "url": source.url,
        "image_url": source.image_url,
        "thumbnail_url": source.thumbnail_url,
        "published_at": source.published_at.isoformat() if source.published_at else None,
        "content_type": source.content_type.value if source.content_type else None,
    }
    if source.briefing_context:
        payload["briefing_context"] = source.briefing_context
    return payload
