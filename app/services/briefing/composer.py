from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from openai import OpenAI
from pydantic import ValidationError
from pydantic_ai.agent import AgentRunResult

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.services.briefing.layout_models import (
    ComposerBlock,
    ComposerLayout,
    FigureBlock,
    PassageBlock,
    PullquoteBlock,
)
from app.services.briefing.layout_policy import (
    BriefingLayoutAssessment,
    BriefingLayoutDisposition,
    assess_briefing_layout,
    is_low_signal_generated_text,
)
from app.services.briefing.normalize import (
    NormalizedLayout,
    normalize_layout,
)
from app.services.briefing.repair import repair_layout
from app.services.briefing.sources import BriefingSource
from app.services.llm_agents import get_basic_agent
from app.services.llm_errors import is_llm_unavailable_error
from app.services.llm_models import OPENROUTER_REASONING_CONFIG, openrouter_provider_config
from app.services.prompt_library import render_prompt
from app.services.vendor_costs import extract_usage_from_result, record_vendor_usage_out_of_band
from app.services.vendor_usage import record_model_usage

PROMPT_VERSION = "briefing-v2"
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


def plan_windows(
    sources: list[BriefingSource],
    *,
    tier: str,
    settings: Settings | None = None,
) -> list[list[BriefingSource]]:
    settings = settings or get_settings()
    max_size = settings.briefing_news_window_max if tier == "news" else settings.briefing_window_max
    max_size = max(1, max_size)
    return [sources[index : index + max_size] for index in range(0, len(sources), max_size)]


def compose_window(
    sources: list[BriefingSource],
    *,
    lens_key: str,
    lens_title: str,
    tier: str,
    window_index: int,
    task_id: int | None = None,
    user_id: int | None = None,
    use_llm: bool = True,
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

    if use_llm:
        fallback_reason: str | None = None
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
                    "Briefing LLM composition failed; retrying or falling back",
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
                    if is_llm_unavailable_error(exc):
                        fallback_reason = f"llm_unavailable_fallback:{type(exc).__name__}"
                        break
                    raise BriefingCompositionError(
                        "Briefing LLM composition failed after retries"
                    ) from exc
                warnings.append(f"llm_error_retry:{attempt}")
                continue
            candidate = process_generated_layout(
                llm_blocks,
                sources=sources,
                lens_key=lens_key,
                window_index=window_index,
                figure_budget=figure_budget,
                ensure_source_figures=tier != "news",
            )
            if candidate.accepted:
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
                    },
                },
            )
            if attempt >= MAX_COMPOSE_ATTEMPTS:
                raise BriefingCompositionInvalidOutput(
                    "Briefing LLM layout failed policy after retries"
                )
            warnings.append(f"llm_layout_policy_retry:{attempt}")
        if fallback_reason is not None:
            logger.warning(
                "Briefing LLM composition fell back to deterministic layout",
                extra={
                    "component": "briefing",
                    "operation": "compose_window",
                    "task_id": task_id,
                    "item_id": user_id,
                    "context_data": {
                        "lens_key": lens_key,
                        "tier": tier,
                        "window_index": window_index,
                        "fallback_reason": fallback_reason,
                    },
                },
            )
            model_spec = "deterministic"
            warnings.append(fallback_reason)
            processed = process_generated_layout(
                deterministic_layout(sources, lens_title=lens_title, tier=tier).model_dump(
                    mode="json"
                )["blocks"],
                sources=sources,
                lens_key=lens_key,
                window_index=window_index,
                figure_budget=figure_budget,
                ensure_source_figures=tier != "news",
            )
    else:
        model_spec = "deterministic"
        processed = process_generated_layout(
            deterministic_layout(sources, lens_title=lens_title, tier=tier).model_dump(mode="json")[
                "blocks"
            ],
            sources=sources,
            lens_key=lens_key,
            window_index=window_index,
            figure_budget=figure_budget,
            ensure_source_figures=tier != "news",
        )

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
    lens_key: str,
    window_index: int,
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
        lens_key=lens_key,
        window_index=window_index,
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


def deterministic_layout(
    sources: list[BriefingSource],
    *,
    lens_title: str,
    tier: str,
) -> ComposerLayout:
    sentences = [_source_sentence(source, index=index) for index, source in enumerate(sources)]
    source_label = "source" if len(sources) == 1 else "sources"
    intro = f"**{lens_title}** opens with {len(sources)} unread {source_label}."
    markdown = intro + " " + " ".join(sentences)
    blocks: list[ComposerBlock] = [
        PassageBlock(type="passage", markdown=markdown, weight="feature")
    ]
    first_image = next(
        (source for source in sources if source.image_url or source.thumbnail_url), None
    )
    if first_image is not None:
        blocks.append(
            FigureBlock(
                type="figure",
                source_key=first_image.source_key,
                caption=first_image.title,
                # Inset figures float inside the adjacent passage on the client.
                placement="inset",
            )
        )
    pullquote_source = next((source for source in sources if source.key_points), None)
    if pullquote_source is not None:
        blocks.append(
            PullquoteBlock(
                type="pullquote",
                source_key=pullquote_source.source_key,
                text=pullquote_source.key_points[0],
            )
        )
    return ComposerLayout(blocks=blocks)


def _source_sentence(source: BriefingSource, *, index: int) -> str:
    url_kind = "content" if source.kind == "content" else "news"
    summary = source.summary or (source.key_points[0] if source.key_points else "is ready to read")
    insight_open = f"{{{{insight:source_{index}}}}}" if index < 3 else ""
    insight_close = "{{/insight}}" if index < 3 else ""
    return (
        f"[{source.title}](newsly://briefing/{url_kind}/{source.id}) "
        f"{insight_open}{summary}{insight_close}"
    )


def generate_layout_with_llm(
    sources: list[BriefingSource],
    *,
    lens_title: str,
    tier: str,
    model_spec: str,
    timeout_seconds: int,
    task_id: int | None,
    user_id: int | None,
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
        [block.model_dump(mode="json", exclude_none=True) for block in result.output.blocks],
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
) -> tuple[list[dict[str, Any]], dict[str, int | None] | None]:
    settings = get_settings()
    api_key = settings.openrouter_api_key
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not configured in settings.")
    model_name = model_spec.split(":", 1)[1]
    request_timeout = httpx.Timeout(
        timeout_seconds,
        connect=10.0,
        read=float(timeout_seconds),
        write=10.0,
        pool=10.0,
    )
    client = OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        timeout=request_timeout,
        max_retries=0,
        http_client=httpx.Client(timeout=request_timeout),
    )
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "ComposerLayout",
                    "strict": True,
                    "schema": ComposerLayout.model_json_schema(),
                },
            },
            extra_body={
                "provider": openrouter_provider_config(),
                "reasoning": OPENROUTER_REASONING_CONFIG,
            },
            timeout=request_timeout,
        )
    finally:
        client.close()
    content = response.choices[0].message.content
    if not content:
        raise BriefingCompositionInvalidOutput("OpenRouter returned an empty briefing response")
    layout = _parse_composer_layout_json(content)
    usage = _usage_from_openrouter_response(response)
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
        [block.model_dump(mode="json", exclude_none=True) for block in layout.blocks],
        usage,
    )


def _parse_composer_layout_json(content: str) -> ComposerLayout:
    payload = json.loads(_strip_json_code_fence(content))
    if isinstance(payload, list):
        return ComposerLayout(blocks=[_coerce_composer_block(block) for block in payload])
    if isinstance(payload, dict):
        blocks = payload.get("blocks")
        if blocks is None:
            blocks = payload.get("layout")
        if isinstance(blocks, list):
            payload = {"blocks": [_coerce_composer_block(block) for block in blocks]}
    return ComposerLayout.model_validate(payload)


def _coerce_composer_block(block: Any) -> Any:
    if not isinstance(block, dict):
        return block
    coerced = dict(block)
    content = coerced.pop("content", None)
    _recover_weight_payload(coerced)
    block_type = str(coerced.get("type") or "").strip().lower()
    if isinstance(content, str) and content.strip():
        if block_type == "pullquote" and not coerced.get("text"):
            coerced["text"] = content
        elif block_type == "figure" and not coerced.get("caption"):
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
    if block_type == "pullquote":
        block["text"] = raw_weight.strip()
    elif block_type == "figure":
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


def _strip_json_code_fence(content: str) -> str:
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if not lines or not lines[0].strip().startswith("```"):
        return stripped
    if lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _usage_from_openrouter_response(response: object) -> dict[str, int | None] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    input_tokens = getattr(usage, "prompt_tokens", None)
    output_tokens = getattr(usage, "completion_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    return {
        "input_tokens": input_tokens,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


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
