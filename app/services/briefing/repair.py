from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.briefing.normalize import close_unpaired_insights, source_keys_in_markdown
from app.services.briefing.sources import BriefingSource


@dataclass(frozen=True)
class RepairResult:
    blocks: list[dict[str, Any]]
    warnings: list[str]


def repair_layout(
    blocks: list[dict[str, Any]],
    *,
    sources: list[BriefingSource],
    lens_key: str,
    window_index: int,
    figure_budget: int,
) -> RepairResult:
    """Apply deterministic guardrails to an LLM-produced flat briefing layout."""

    source_by_key = {source.source_key: source for source in sources}
    repaired: list[dict[str, Any]] = []
    warnings: list[str] = []
    figures_used = 0

    for raw in blocks:
        block = dict(raw)
        block_type = str(block.get("type") or "").strip().lower()
        if block_type not in {"passage", "figure", "pullquote"}:
            warnings.append(f"unknown_block_type:{block_type or 'missing'}")
            continue
        block["type"] = block_type
        if block_type == "figure":
            if not repaired:
                warnings.append("leading_figure_dropped")
                continue
            source_key = str(block.get("source_key") or "")
            source = source_by_key.get(source_key)
            if source is None:
                warnings.append("figure_unknown_source")
                continue
            if not (source.image_url or source.thumbnail_url):
                warnings.append("figure_source_without_image")
                continue
            if figures_used >= figure_budget:
                warnings.append("figure_budget_exceeded")
                continue
            block["placement"] = "inset" if block.get("placement") == "inset" else "full"
            block["image_url"] = block.get("image_url") or source.image_url
            block["thumbnail_url"] = block.get("thumbnail_url") or source.thumbnail_url
            figures_used += 1
        elif block_type == "pullquote":
            text = _clean_text(block.get("text"))
            if not text:
                warnings.append("empty_pullquote")
                continue
            block["text"] = _strip_heading_noise(text)[:360]
            source_key = str(block.get("source_key") or "")
            if source_key and source_key not in source_by_key:
                block["source_key"] = None
                warnings.append("pullquote_unknown_source_stripped")
        else:
            markdown = _clean_text(block.get("markdown") or block.get("text"))
            if not markdown:
                warnings.append("empty_passage")
                continue
            block["markdown"] = close_unpaired_insights(markdown)
            if "markdown" not in raw and raw.get("text"):
                warnings.append("passage_text_field_recovered")
        repaired.append(block)

    cited = set()
    for block in repaired:
        if block.get("type") == "passage":
            cited.update(source_keys_in_markdown(str(block.get("markdown") or "")))
    missing = [source for source in sources if source.source_key not in cited]
    if missing:
        repaired.append(
            {
                "type": "passage",
                "weight": "brief",
                "markdown": " ".join(_deterministic_source_sentence(source) for source in missing),
            }
        )
        warnings.append(f"coverage_repair:{len(missing)}")

    prefixed = _prefix_insight_ids(repaired, prefix=f"{lens_key}_w{window_index}_")
    return RepairResult(blocks=prefixed, warnings=warnings)


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


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _strip_heading_noise(value: str) -> str:
    cleaned = value.strip()
    while cleaned.startswith(("#", ">", "-", "Quote:", "Pullquote:")):
        cleaned = cleaned.lstrip("#>- ").removeprefix("Quote:").removeprefix("Pullquote:").strip()
    return cleaned
