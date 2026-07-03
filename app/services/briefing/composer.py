from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.agent import AgentRunResult

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.services.briefing.normalize import NormalizedLayout, normalize_layout
from app.services.briefing.repair import repair_layout
from app.services.briefing.sources import BriefingSource
from app.services.llm_agents import get_basic_agent
from app.services.prompt_library import render_prompt
from app.services.vendor_costs import extract_usage_from_result
from app.services.vendor_usage import record_model_usage

PROMPT_VERSION = "briefing-v1"
LLM_ATTEMPTS = 2
logger = get_logger(__name__)


class ComposerBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    weight: str | None = None
    markdown: str | None = None
    source_key: str | None = None
    caption: str | None = None
    placement: str | None = None
    text: str | None = None


class ComposerLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[ComposerBlock] = Field(default_factory=list, min_length=1)


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
) -> ComposedSegment:
    settings = settings or get_settings()
    started_at = time.perf_counter()
    model_spec = settings.briefing_model
    warnings: list[str] = []
    input_tokens: int | None = None
    output_tokens: int | None = None
    blocks: list[dict[str, Any] | ComposerBlock]

    try:
        if use_llm:
            llm_blocks, usage = _compose_window_with_llm(
                sources,
                lens_title=lens_title,
                tier=tier,
                model_spec=model_spec,
                timeout_seconds=settings.briefing_llm_timeout_seconds,
                task_id=task_id,
                user_id=user_id,
            )
            blocks = list(llm_blocks)
            if usage:
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
        else:
            blocks = list(deterministic_layout(sources, lens_title=lens_title, tier=tier).blocks)
            model_spec = "deterministic"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Briefing composer fell back to deterministic layout",
            exc_info=True,
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
        )
        warnings.append(f"llm_fallback:{type(exc).__name__}")
        blocks = list(deterministic_layout(sources, lens_title=lens_title, tier=tier).blocks)
        model_spec = "deterministic"

    figure_budget = (
        settings.briefing_max_figures_news if tier == "news" else settings.briefing_max_figures_deep
    )
    repaired = repair_layout(
        [
            block.model_dump(mode="json", exclude_none=True)
            if isinstance(block, ComposerBlock)
            else block
            for block in blocks
        ],
        sources=sources,
        lens_key=lens_key,
        window_index=window_index,
        figure_budget=figure_budget,
    )
    warnings.extend(repaired.warnings)
    normalized: NormalizedLayout = normalize_layout(
        repaired.blocks,
        source_keys={source.source_key for source in sources},
    )
    warnings.extend(normalized.warnings)
    status = "active" if normalized.blocks else "degraded"
    if status == "degraded":
        normalized = normalize_layout(
            deterministic_layout(sources, lens_title=lens_title, tier=tier).model_dump(mode="json")[
                "blocks"
            ],
            source_keys={source.source_key for source in sources},
        )
        warnings.append("degraded_deterministic_recovery")
    generation_ms = round((time.perf_counter() - started_at) * 1000)
    return ComposedSegment(
        blocks=normalized.blocks,
        markdown_raw=normalized.markdown_raw,
        narration_text=normalized.narration_text,
        status=status,
        model=model_spec,
        prompt_version=PROMPT_VERSION,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        generation_ms=generation_ms,
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
        ComposerBlock(type="passage", weight="feature", markdown=markdown)
    ]
    first_image = next(
        (source for source in sources if source.image_url or source.thumbnail_url), None
    )
    if first_image is not None:
        blocks.append(
            ComposerBlock(
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
            ComposerBlock(
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


def _compose_window_with_llm(
    sources: list[BriefingSource],
    *,
    lens_title: str,
    tier: str,
    model_spec: str,
    timeout_seconds: int,
    task_id: int | None,
    user_id: int | None,
) -> tuple[list[dict[str, Any]], dict[str, int | None] | None]:
    system_prompt = _system_prompt(tier)
    user_prompt = render_prompt(
        "briefing/layout#window",
        lens_title=lens_title,
        tier=tier,
        source_payload_json=json.dumps(
            [_source_payload(source) for source in sources],
            ensure_ascii=False,
            indent=2,
        ),
    )

    last_error: Exception | None = None
    for _attempt in range(LLM_ATTEMPTS):
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_run_agent, model_spec, system_prompt, user_prompt)
        try:
            result = future.result(timeout=timeout_seconds)
        except TimeoutError as exc:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            last_error = exc
            continue
        finally:
            if future.done():
                executor.shutdown(wait=True, cancel_futures=True)
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
    if last_error:
        raise last_error
    raise RuntimeError("Briefing composer failed without an error")


def _run_agent(
    model_spec: str, system_prompt: str, user_prompt: str
) -> AgentRunResult[ComposerLayout]:
    agent = get_basic_agent(model_spec, ComposerLayout, system_prompt)
    return agent.run_sync(user_prompt)


def _system_prompt(tier: str) -> str:
    return render_prompt("briefing/layout#system", tier=tier)


def _source_payload(source: BriefingSource) -> dict[str, Any]:
    return {
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
