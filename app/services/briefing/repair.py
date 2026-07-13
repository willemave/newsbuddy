from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.briefing.figure_placement import canonical_figure_placement
from app.services.briefing.layout_policy import (
    BriefingBlockRepair,
    BriefingBlockRepairAction,
    BriefingLayoutAssessment,
    BriefingLayoutDisposition,
    assess_briefing_layout,
    clean_pullquote_text,
)
from app.services.briefing.normalize import close_unpaired_insights, source_keys_in_markdown
from app.services.briefing.sources import BriefingSource

_EM_DASH_RANGE_RE = re.compile(r"(?<=\d)\s*—\s*(?=\d)")
_EM_DASH_RE = re.compile(r"\s*—\s*")


@dataclass(frozen=True)
class RepairResult:
    blocks: list[dict[str, Any]]
    warnings: list[str]


class BriefingLayoutRepairError(ValueError):
    """Raised when deterministic repair would hide semantic corruption."""


def repair_layout(
    blocks: list[dict[str, Any]],
    *,
    sources: list[BriefingSource],
    lens_key: str,
    window_index: int,
    figure_budget: int,
    ensure_source_figures: bool = False,
    assessment: BriefingLayoutAssessment | None = None,
) -> RepairResult:
    """Apply deterministic guardrails to an LLM-produced flat briefing layout."""

    source_by_key = {source.source_key: source for source in sources}
    assessment = assessment or assess_briefing_layout(
        blocks,
        source_keys=set(source_by_key),
        source_keys_with_images={
            source.source_key for source in sources if source.image_url or source.thumbnail_url
        },
        figure_budget=figure_budget,
    )
    if assessment.disposition == BriefingLayoutDisposition.RETRY:
        raise BriefingLayoutRepairError(
            "Briefing layout requires regeneration: " + ", ".join(assessment.issues)
        )
    repaired: list[dict[str, Any]] = []
    warnings: list[str] = []
    figures_used = 0
    repairs_by_index: dict[int, list[BriefingBlockRepair]] = {}
    for block_repair in assessment.block_repairs:
        repairs_by_index.setdefault(block_repair.block_index, []).append(block_repair)

    for index, raw in enumerate(blocks):
        block = dict(raw)
        block_repairs = repairs_by_index.get(index, [])
        drop = next(
            (
                repair
                for repair in block_repairs
                if repair.action == BriefingBlockRepairAction.DROP_BLOCK
            ),
            None,
        )
        if drop is not None:
            warnings.append(drop.warning)
            continue
        block_type = str(block.get("type") or "").strip().lower()
        if block_type not in {"passage", "figure", "pullquote"}:
            raise BriefingLayoutRepairError("Layout assessment allowed an unknown block type")
        block["type"] = block_type
        if block_type == "figure":
            source_key = str(block.get("source_key") or "")
            source = source_by_key.get(source_key)
            if source is None:
                raise BriefingLayoutRepairError("Layout assessment allowed an unusable figure")
            block["placement"] = canonical_figure_placement(block.get("placement")).value
            block["image_url"] = block.get("image_url") or source.image_url
            block["thumbnail_url"] = block.get("thumbnail_url") or source.thumbnail_url
            strip_caption = next(
                (
                    repair
                    for repair in block_repairs
                    if repair.action == BriefingBlockRepairAction.STRIP_CAPTION
                ),
                None,
            )
            if strip_caption is not None:
                block["caption"] = None
                warnings.append(strip_caption.warning)
            elif isinstance(block.get("caption"), str):
                caption = _clean_text(block["caption"])
                if caption:
                    block["caption"] = _replace_em_dashes(caption)
            figures_used += 1
        elif block_type == "pullquote":
            text = _clean_text(block.get("text"))
            if not text:
                raise BriefingLayoutRepairError("Layout assessment allowed an empty pullquote")
            text = clean_pullquote_text(text)
            block["text"] = _replace_em_dashes(text[:360])
            strip_source_key = next(
                (
                    repair
                    for repair in block_repairs
                    if repair.action == BriefingBlockRepairAction.STRIP_SOURCE_KEY
                ),
                None,
            )
            if strip_source_key is not None:
                block["source_key"] = None
                warnings.append(strip_source_key.warning)
        else:
            markdown = _clean_text(block.get("markdown") or block.get("text"))
            if not markdown:
                raise BriefingLayoutRepairError("Layout assessment allowed an empty passage")
            block["markdown"] = _replace_em_dashes(close_unpaired_insights(markdown))
            if "markdown" not in raw and raw.get("text"):
                warnings.append("passage_text_field_recovered")
        repaired.append(block)

    missing_source_keys = set(assessment.coverage.missing_source_keys)
    missing = [source for source in sources if source.source_key in missing_source_keys]
    if missing:
        repaired.append(
            {
                "type": "passage",
                "weight": "brief",
                "markdown": " ".join(_deterministic_source_sentence(source) for source in missing),
            }
        )
        warnings.append(f"coverage_repair:{len(missing)}")

    if ensure_source_figures and repaired:
        backfilled = _backfill_source_figures(
            repaired,
            sources=sources,
            budget_remaining=figure_budget - figures_used,
        )
        if backfilled:
            warnings.append(f"figure_backfill:{backfilled}")

    prefixed = _prefix_insight_ids(repaired, prefix=f"{lens_key}_w{window_index}_")
    return RepairResult(blocks=prefixed, warnings=warnings)


def _backfill_source_figures(
    blocks: list[dict[str, Any]],
    *,
    sources: list[BriefingSource],
    budget_remaining: int,
) -> int:
    """Insert an inset figure after the citing passage for imaged sources the LLM skipped."""

    figured = {block.get("source_key") for block in blocks if block.get("type") == "figure"}
    added = 0
    for source in sources:
        if budget_remaining - added <= 0:
            break
        if source.source_key in figured:
            continue
        if not (source.image_url or source.thumbnail_url):
            continue
        insert_at = _figure_insert_index(blocks, source_key=source.source_key)
        blocks.insert(
            insert_at,
            {
                "type": "figure",
                "source_key": source.source_key,
                "caption": source.title,
                "placement": "inset",
                "image_url": source.image_url,
                "thumbnail_url": source.thumbnail_url,
            },
        )
        added += 1
    return added


def _figure_insert_index(blocks: list[dict[str, Any]], *, source_key: str) -> int:
    for index, block in enumerate(blocks):
        if block.get("type") != "passage":
            continue
        if source_key not in source_keys_in_markdown(str(block.get("markdown") or "")):
            continue
        # Skip past figures already sitting after the passage to keep source order.
        insert_at = index + 1
        while insert_at < len(blocks) and blocks[insert_at].get("type") == "figure":
            insert_at += 1
        return insert_at
    return len(blocks)


def _deterministic_source_sentence(source: BriefingSource) -> str:
    url_kind = "content" if source.kind == "content" else "news"
    sentence = source.summary or (source.key_points[0] if source.key_points else "is ready to read")
    return f"[{source.title}](newsly://briefing/{url_kind}/{source.id}) {sentence}"


def _prefix_insight_ids(blocks: list[dict[str, Any]], *, prefix: str) -> list[dict[str, Any]]:
    prefixed: list[dict[str, Any]] = []
    for raw in blocks:
        block = dict(raw)
        if block.get("type") == "passage":
            markdown = str(block.get("markdown") or "")
            block["markdown"] = markdown.replace("{{insight:", "{{insight:" + prefix)
        prefixed.append(block)
    return prefixed


def _replace_em_dashes(text: str) -> str:
    """Style backstop for the prompt's no-em-dash rule: numeric ranges become
    hyphens, everything else becomes a comma pause."""

    text = _EM_DASH_RANGE_RE.sub("-", text)
    return _EM_DASH_RE.sub(", ", text)


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None
