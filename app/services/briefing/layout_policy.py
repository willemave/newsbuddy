"""Canonical structural policy for generated and normalized Briefing layouts."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.briefing.normalize import source_keys_in_markdown

_PROTOCOL_MARKER_RE = re.compile(r"\{\{/?[a-zA-Z0-9_.:-]+(?::[^}]*)?\}\}")
_SOURCE_KEY_ECHO_RE = re.compile(
    r'^(?:"?source[_ ]key"?\s*:\s*"?)?(?:content|news):\d+"?,?$',
    re.IGNORECASE,
)
_LOW_SIGNAL_VALUE_DUMPS = frozenset(
    {
        "content",
        "content:",
        "high",
        "important",
        "low",
        "markdown",
        "markdowns",
        "medium",
        "normal",
        "optional",
        "primary",
        "regular",
        "supporting",
    }
)
_BLOCK_TYPES = frozenset({"passage", "figure", "pullquote"})


class BriefingLayoutDisposition(StrEnum):
    ACCEPT = "accept"
    REPAIR = "repair"
    RETRY = "retry"


class BriefingBlockRepairAction(StrEnum):
    DROP_BLOCK = "drop_block"
    STRIP_CAPTION = "strip_caption"
    STRIP_SOURCE_KEY = "strip_source_key"


class BriefingBlockRepair(BaseModel):
    """A deterministic block mutation selected by the canonical policy."""

    model_config = ConfigDict(frozen=True)

    block_index: int = Field(ge=0)
    action: BriefingBlockRepairAction
    warning: str


class BriefingSourceCoverage(BaseModel):
    model_config = ConfigDict(frozen=True)

    expected_source_keys: list[str] = Field(default_factory=list)
    covered_source_keys: list[str] = Field(default_factory=list)
    missing_source_keys: list[str] = Field(default_factory=list)


class BriefingLayoutAssessment(BaseModel):
    """One policy decision plus the evidence that produced it."""

    model_config = ConfigDict(frozen=True)

    disposition: BriefingLayoutDisposition
    issues: list[str] = Field(default_factory=list)
    coverage: BriefingSourceCoverage
    has_usable_passage: bool
    low_signal_values: list[str] = Field(default_factory=list)
    unknown_source_keys: list[str] = Field(default_factory=list)
    repairable_unknown_source_keys: list[str] = Field(default_factory=list)
    block_repairs: list[BriefingBlockRepair] = Field(default_factory=list)

    @property
    def layout_valid(self) -> bool:
        return self.disposition == BriefingLayoutDisposition.ACCEPT


def assess_briefing_layout(
    blocks: list[dict[str, Any]],
    *,
    source_keys: set[str],
    source_keys_with_images: set[str] | None = None,
    figure_budget: int | None = None,
) -> BriefingLayoutAssessment:
    """Classify a layout as ready, deterministically repairable, or requiring retry."""
    passage_source_keys: set[str] = set()
    unknown_source_keys: set[str] = set()
    repairable_unknown_source_keys: set[str] = set()
    block_repairs: list[BriefingBlockRepair] = []
    low_signal_values: set[str] = set()
    issues: list[str] = []
    repair_required = False
    retry_required = False
    has_usable_passage = False
    has_retained_block = False
    retained_figures = 0

    for index, block in enumerate(blocks):
        block_type = str(block.get("type") or "").strip().lower()
        if block_type not in _BLOCK_TYPES:
            issues.append(f"unknown_block_type:{index}:{block_type or 'missing'}")
            retry_required = True
            continue

        visible_text = _visible_block_text(block, block_type=block_type)
        if block_type == "pullquote":
            visible_text = clean_pullquote_text(visible_text)
        normalized_text = normalize_low_signal_value(visible_text)
        block_source_key = _clean_source_key(block.get("source_key"))

        if block_type == "passage":
            linked_source_keys = _passage_source_keys(block)
            passage_source_keys.update(linked_source_keys)
            unknown_passage_keys = linked_source_keys - source_keys
            if unknown_passage_keys:
                unknown_source_keys.update(unknown_passage_keys)
                issues.append(
                    "unknown_passage_source_references:" + ",".join(sorted(unknown_passage_keys))
                )
                retry_required = True

            low_signal = not linked_source_keys and is_low_signal_generated_text(
                visible_text,
                allow_source_links=True,
            )
            if not visible_text.strip():
                issues.append(f"empty_passage:{index}")
                block_repairs.append(
                    BriefingBlockRepair(
                        block_index=index,
                        action=BriefingBlockRepairAction.DROP_BLOCK,
                        warning="empty_passage",
                    )
                )
                repair_required = True
            elif low_signal:
                if normalized_text:
                    low_signal_values.add(normalized_text)
                issues.append(f"low_signal_passage:{index}")
                block_repairs.append(
                    BriefingBlockRepair(
                        block_index=index,
                        action=BriefingBlockRepairAction.DROP_BLOCK,
                        warning="low_signal_passage_dropped",
                    )
                )
                repair_required = True
            else:
                has_usable_passage = True
                has_retained_block = True
            continue

        if block_type == "pullquote":
            if not visible_text.strip():
                issues.append(f"empty_pullquote:{index}")
                block_repairs.append(
                    BriefingBlockRepair(
                        block_index=index,
                        action=BriefingBlockRepairAction.DROP_BLOCK,
                        warning="empty_pullquote",
                    )
                )
                repair_required = True
            elif is_low_signal_generated_text(visible_text, allow_source_links=False):
                if normalized_text:
                    low_signal_values.add(normalized_text)
                issues.append(f"low_signal_pullquote:{index}")
                block_repairs.append(
                    BriefingBlockRepair(
                        block_index=index,
                        action=BriefingBlockRepairAction.DROP_BLOCK,
                        warning="low_signal_pullquote_dropped",
                    )
                )
                repair_required = True
            else:
                has_retained_block = True
            if block_source_key and block_source_key not in source_keys:
                repairable_unknown_source_keys.add(block_source_key)
                issues.append(f"pullquote_unknown_source:{index}:{block_source_key}")
                block_repairs.append(
                    BriefingBlockRepair(
                        block_index=index,
                        action=BriefingBlockRepairAction.STRIP_SOURCE_KEY,
                        warning="pullquote_unknown_source_stripped",
                    )
                )
                repair_required = True
            continue

        figure_drop_warning: str | None = None
        if not has_retained_block:
            issues.append("leading_figure")
            figure_drop_warning = "leading_figure_dropped"
            repair_required = True
        if not block_source_key:
            issues.append(f"figure_missing_source:{index}")
            figure_drop_warning = figure_drop_warning or "figure_unknown_source"
            repair_required = True
        elif block_source_key not in source_keys:
            repairable_unknown_source_keys.add(block_source_key)
            issues.append(f"figure_unknown_source:{index}:{block_source_key}")
            figure_drop_warning = figure_drop_warning or "figure_unknown_source"
            repair_required = True
        elif (
            source_keys_with_images is not None and block_source_key not in source_keys_with_images
        ):
            issues.append(f"figure_source_without_image:{index}:{block_source_key}")
            figure_drop_warning = figure_drop_warning or "figure_source_without_image"
            repair_required = True
        elif figure_budget is not None and retained_figures >= figure_budget:
            issues.append(f"figure_budget_exceeded:{index}")
            figure_drop_warning = figure_drop_warning or "figure_budget_exceeded"
            repair_required = True
        if figure_drop_warning:
            block_repairs.append(
                BriefingBlockRepair(
                    block_index=index,
                    action=BriefingBlockRepairAction.DROP_BLOCK,
                    warning=figure_drop_warning,
                )
            )
            continue
        if visible_text.strip() and is_low_signal_generated_text(
            visible_text,
            allow_source_links=False,
        ):
            if normalized_text:
                low_signal_values.add(normalized_text)
            issues.append(f"low_signal_figure_caption:{index}")
            block_repairs.append(
                BriefingBlockRepair(
                    block_index=index,
                    action=BriefingBlockRepairAction.STRIP_CAPTION,
                    warning="low_signal_figure_caption_stripped",
                )
            )
            repair_required = True
        retained_figures += 1
        has_retained_block = True

    covered_source_keys = passage_source_keys & source_keys
    missing_source_keys = source_keys - covered_source_keys
    if missing_source_keys:
        issues.append("missing_source_coverage:" + ",".join(sorted(missing_source_keys)))
        repair_required = True
    if not has_usable_passage:
        issues.append("missing_usable_passage")
        retry_required = True

    if retry_required:
        disposition = BriefingLayoutDisposition.RETRY
    elif repair_required:
        disposition = BriefingLayoutDisposition.REPAIR
    else:
        disposition = BriefingLayoutDisposition.ACCEPT

    return BriefingLayoutAssessment(
        disposition=disposition,
        issues=issues,
        coverage=BriefingSourceCoverage(
            expected_source_keys=sorted(source_keys),
            covered_source_keys=sorted(covered_source_keys),
            missing_source_keys=sorted(missing_source_keys),
        ),
        has_usable_passage=has_usable_passage,
        low_signal_values=sorted(low_signal_values),
        unknown_source_keys=sorted(unknown_source_keys),
        repairable_unknown_source_keys=sorted(repairable_unknown_source_keys),
        block_repairs=block_repairs,
    )


def is_low_signal_generated_text(value: str, *, allow_source_links: bool) -> bool:
    """Detect schema/control values that a provider emitted as visible prose."""
    if allow_source_links and source_keys_in_markdown(value):
        return False
    cleaned = _debris_text(value)
    if not cleaned:
        return True
    normalized = normalize_low_signal_value(cleaned)
    return (
        normalized in _LOW_SIGNAL_VALUE_DUMPS
        or bool(re.fullmatch(r"[01](?:\.0+)?(?:\s+[01](?:\.0+)?)*", normalized))
        or bool(_SOURCE_KEY_ECHO_RE.fullmatch(cleaned.strip()))
    )


def normalize_low_signal_value(value: str) -> str:
    text = _debris_text(str(value))
    return text.casefold().strip(" .,:;")


def clean_pullquote_text(value: str) -> str:
    """Normalize pullquote wrappers before policy classification and rendering."""

    cleaned = " ".join(value.split()).strip()
    while cleaned.startswith(("#", ">", "-", "Quote:", "Pullquote:")):
        cleaned = cleaned.lstrip("#>- ").removeprefix("Quote:").removeprefix("Pullquote:").strip()
    return cleaned


def _visible_block_text(block: dict[str, Any], *, block_type: str) -> str:
    if block_type == "passage":
        markdown = block.get("markdown") or block.get("text")
        if isinstance(markdown, str):
            return markdown
        return " ".join(str(run.get("text") or "") for run in _paragraph_runs(block)).strip()
    value = block.get("text") if block_type == "pullquote" else block.get("caption")
    return value if isinstance(value, str) else ""


def _passage_source_keys(block: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    markdown = block.get("markdown")
    if isinstance(markdown, str):
        keys.update(source_keys_in_markdown(markdown))
    for run in _paragraph_runs(block):
        if str(run.get("kind") or "") != "source_link":
            continue
        source_key = _clean_source_key(run.get("source_key"))
        if source_key:
            keys.add(source_key)
    return keys


def _paragraph_runs(block: dict[str, Any]) -> list[dict[str, Any]]:
    paragraphs = block.get("paragraphs")
    if not isinstance(paragraphs, list):
        return []
    return [
        run
        for paragraph in paragraphs
        if isinstance(paragraph, dict) and isinstance(paragraph.get("runs"), list)
        for run in paragraph["runs"]
        if isinstance(run, dict)
    ]


def _clean_source_key(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _debris_text(value: str) -> str:
    text = _PROTOCOL_MARKER_RE.sub("", value)
    return re.sub(r"[`*_>#\-\s]+", " ", text).strip()
