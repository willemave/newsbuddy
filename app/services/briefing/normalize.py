from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models.contracts import BriefingBlockType, BriefingRunKind
from app.services.briefing.figure_placement import canonical_figure_placement
from app.services.briefing.source_keys import build_source_key

LINK_RE = re.compile(r"\[([^\]]+)\]\(((?:newsly|news)://briefing/(content|news)/(\d+))\)")
INSIGHT_MARKER_RE = re.compile(r"\{\{/?insight(?::[^{}]*)?\}\}")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
# Bold markers wrapping a source link (**[title](url)**) are not representable
# as runs — links already render emphasized — so unwrap them before parsing.
BOLD_LINK_RE = re.compile(r"\*\*\s*(\[[^\]]+\]\([^)]+\))\s*\*\*")


@dataclass(frozen=True)
class NormalizedLayout:
    blocks: list[dict[str, Any]]
    narration_text: str
    markdown_raw: str
    warnings: list[str]


def strip_insight_markers(text: str) -> str:
    """Flatten obsolete Briefing insight markup to ordinary text."""

    return INSIGHT_MARKER_RE.sub("", text)


def normalize_layout(
    blocks: list[dict[str, Any]],
    *,
    source_keys: set[str],
) -> NormalizedLayout:
    normalized_blocks: list[dict[str, Any]] = []
    narration_parts: list[str] = []
    raw_parts: list[str] = []
    warnings: list[str] = []

    for raw_block in blocks:
        block_type = str(raw_block.get("type") or "").strip().lower()
        if block_type == BriefingBlockType.PASSAGE.value:
            markdown = str(raw_block.get("markdown") or raw_block.get("text") or "").strip()
            if not markdown:
                warnings.append("empty_passage_dropped")
                continue
            markdown = strip_insight_markers(markdown)
            paragraphs = _paragraphs_from_markdown(markdown, source_keys=source_keys)
            if not paragraphs:
                warnings.append("passage_without_runs_dropped")
                continue
            normalized_blocks.append(
                {
                    "type": BriefingBlockType.PASSAGE.value,
                    "weight": _normalized_weight(raw_block.get("weight")),
                    "paragraphs": paragraphs,
                    "source_key": None,
                    "image_url": None,
                    "thumbnail_url": None,
                    "caption": None,
                    "placement": None,
                    "text": None,
                }
            )
            raw_parts.append(markdown)
            narration = markdown_to_narration(markdown)
            if narration:
                narration_parts.append(narration)
        elif block_type == BriefingBlockType.FIGURE.value:
            source_key = _valid_source_key(raw_block.get("source_key"), source_keys)
            if source_key is None:
                warnings.append("figure_without_valid_source_dropped")
                continue
            normalized_blocks.append(
                {
                    "type": BriefingBlockType.FIGURE.value,
                    "weight": None,
                    "paragraphs": None,
                    "source_key": source_key,
                    "image_url": _clean_str(raw_block.get("image_url")),
                    "thumbnail_url": _clean_str(raw_block.get("thumbnail_url")),
                    "caption": _clean_str(raw_block.get("caption")),
                    "placement": _normalized_placement(raw_block.get("placement")),
                    "text": None,
                }
            )
        elif block_type == BriefingBlockType.PULLQUOTE.value:
            text = _clean_str(raw_block.get("text"))
            source_key = _valid_source_key(raw_block.get("source_key"), source_keys)
            if not text:
                warnings.append("empty_pullquote_dropped")
                continue
            normalized_blocks.append(
                {
                    "type": BriefingBlockType.PULLQUOTE.value,
                    "weight": None,
                    "paragraphs": None,
                    "source_key": source_key,
                    "image_url": None,
                    "thumbnail_url": None,
                    "caption": None,
                    "placement": None,
                    "text": text[:360],
                }
            )
        else:
            warnings.append(f"unknown_block_type_dropped:{block_type or 'missing'}")

    return NormalizedLayout(
        blocks=normalized_blocks,
        narration_text="\n\n".join(narration_parts).strip(),
        markdown_raw="\n\n".join(raw_parts).strip(),
        warnings=warnings,
    )


def markdown_to_narration(markdown: str) -> str:
    text = LINK_RE.sub(lambda match: match.group(1), markdown)
    text = strip_insight_markers(text)
    text = text.replace("**", "")
    return " ".join(text.split()).strip()


def source_keys_in_markdown(markdown: str) -> set[str]:
    keys: set[str] = set()
    for match in LINK_RE.finditer(markdown):
        keys.add(build_source_key(match.group(3), int(match.group(4))))
    return keys


def _paragraphs_from_markdown(
    markdown: str,
    *,
    source_keys: set[str],
) -> list[dict[str, Any]]:
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", markdown) if chunk.strip()]
    paragraphs: list[dict[str, Any]] = []
    for chunk in chunks:
        sentence_groups = _sentence_groups(chunk)
        for group in sentence_groups:
            runs = _runs_from_markdown(group, source_keys=source_keys)
            if runs:
                paragraphs.append({"runs": runs})
    return paragraphs


def _sentence_groups(text: str) -> list[str]:
    sentences = [sentence.strip() for sentence in SENTENCE_RE.split(text) if sentence.strip()]
    if len(sentences) <= 3:
        return [text.strip()]
    groups: list[str] = []
    for index in range(0, len(sentences), 3):
        groups.append(" ".join(sentences[index : index + 3]))
    return groups


def _runs_from_markdown(
    markdown: str,
    *,
    source_keys: set[str],
) -> list[dict[str, Any]]:
    markdown = BOLD_LINK_RE.sub(lambda match: match.group(1), markdown)
    runs: list[dict[str, Any]] = []
    index = 0
    while index < len(markdown):
        next_link = LINK_RE.search(markdown, index)
        candidates: list[tuple[int, str, re.Match[str] | None]] = []
        if next_link:
            candidates.append((next_link.start(), "link", next_link))
        if not candidates:
            _append_text_runs(runs, markdown[index:])
            break
        next_pos, token_type, match = min(candidates, key=lambda item: item[0])
        if next_pos > index:
            _append_text_runs(runs, markdown[index:next_pos])
        if token_type == "link" and match is not None:
            source_key = build_source_key(match.group(3), int(match.group(4)))
            text = match.group(1)
            if source_key in source_keys:
                runs.append(
                    {
                        "kind": BriefingRunKind.SOURCE_LINK.value,
                        "text": text,
                        "source_key": source_key,
                        "bold": False,
                    }
                )
            else:
                _append_text_runs(runs, text)
            index = match.end()
    return _coalesce_runs(runs)


def _append_text_runs(
    runs: list[dict[str, Any]],
    text: str,
) -> None:
    if not text:
        return
    position = 0
    for match in BOLD_RE.finditer(text):
        if match.start() > position:
            _append_plain_run(runs, text[position : match.start()], bold=False)
        _append_plain_run(runs, match.group(1), bold=True)
        position = match.end()
    if position < len(text):
        _append_plain_run(runs, text[position:], bold=False)


def _append_plain_run(
    runs: list[dict[str, Any]],
    text: str,
    *,
    bold: bool,
) -> None:
    if not text:
        return
    runs.append(
        {
            "kind": BriefingRunKind.TEXT.value,
            "text": text,
            "source_key": None,
            "bold": bold,
        }
    )


def _coalesce_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    coalesced: list[dict[str, Any]] = []
    for run in runs:
        if not run.get("text"):
            continue
        previous = coalesced[-1] if coalesced else None
        if (
            previous
            and previous["kind"] == run["kind"]
            and previous.get("source_key") == run.get("source_key")
            and previous.get("bold") == run.get("bold")
        ):
            previous["text"] += run["text"]
        else:
            coalesced.append(run)
    return coalesced


def _valid_source_key(value: Any, source_keys: set[str]) -> str | None:
    text = _clean_str(value)
    if text in source_keys:
        return text
    return None


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _normalized_weight(value: Any) -> str:
    return "feature" if value == "feature" else "brief"


def _normalized_placement(value: Any) -> str:
    return canonical_figure_placement(value).value
