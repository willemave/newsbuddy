"""Generate alternate unread briefing prototype pages.

This script reads the current unread briefing source snapshot and asks an LLM
for two alternate reading experiences:

1. Category-filtered Mad-Lib-style briefing lenses.
2. A personalized Mad-Lib-style briefing using an explicit preference prompt.
"""

from __future__ import annotations

# ruff: noqa: E501
import argparse
import html
import json
import os
import re
import signal
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.model_defaults import OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC
from app.services.llm_agents import get_basic_agent
from app.services.vendor_costs import extract_usage_from_result
from scripts.generate_unread_briefing_prototype import (
    DEFAULT_OUTPUT_DIR,
    GeneratedUnreadBriefing,
    SourceItem,
    build_fallback_repair_chunk,
    merge_usage,
    normalize_generated_briefing,
    render_chunk_html,
    run_windowed_generation,
    validate_generated_sources,
)

DEFAULT_INPUT_JSON = DEFAULT_OUTPUT_DIR / "user_1_current" / "briefing.json"
DEFAULT_PERSONALIZATION_PROMPT = (
    "Filter for Willem as a technical product-builder reader. Prefer developer tools, "
    "AI agents and infrastructure, model/product capability shifts, platform strategy, "
    "security and trust, business model changes, and weird technical artifacts when they "
    "teach something. Deprioritize generic launch announcements, thin rumors, commodity "
    "funding items, culture-war bait, and stories where the title alone is enough. Keep "
    "enough texture that the feed still feels alive, but make the final set faster than "
    "scanning the raw list."
)


class DenseCategoryItem(BaseModel):
    """A compact item inside a dense category scan."""

    model_config = ConfigDict(extra="forbid")

    item_title: str = Field(..., min_length=4, max_length=100)
    compressed_takeaway: str = Field(..., min_length=20, max_length=220)
    why_it_matters: str = Field(..., min_length=12, max_length=180)
    source_keys: list[str] = Field(..., min_length=1, max_length=4)


class DenseCategory(BaseModel):
    """A category grouping several unread sources."""

    model_config = ConfigDict(extra="forbid")

    category_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(..., min_length=3, max_length=70)
    scan_summary: str = Field(..., min_length=20, max_length=180)
    items: list[DenseCategoryItem] = Field(..., min_length=1)


class DenseCategoryBriefing(BaseModel):
    """Dense grouped briefing output."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=5, max_length=120)
    deck: str = Field(..., min_length=20, max_length=240)
    categories: list[DenseCategory] = Field(..., min_length=2, max_length=12)
    omitted_sources: list[str] = Field(default_factory=list)


class PersonalizedBriefingItem(BaseModel):
    """A personalized dense item."""

    model_config = ConfigDict(extra="forbid")

    source_keys: list[str] = Field(..., min_length=1, max_length=3)
    priority: str = Field(..., pattern=r"^(read|skim|skip_context)$")
    headline: str = Field(..., min_length=4, max_length=100)
    value: str = Field(..., min_length=20, max_length=220)
    preference_match: str = Field(..., min_length=12, max_length=180)


class PersonalizedRejectedSource(BaseModel):
    """A source rejected or demoted by the preference prompt."""

    model_config = ConfigDict(extra="forbid")

    source_key: str
    reason: str = Field(..., min_length=8, max_length=180)


class PersonalizedBriefing(BaseModel):
    """Preference-filtered briefing output."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=5, max_length=120)
    deck: str = Field(..., min_length=20, max_length=260)
    applied_preference_summary: str = Field(..., min_length=20, max_length=400)
    read_items: list[PersonalizedBriefingItem] = Field(..., min_length=1, max_length=32)
    skim_items: list[PersonalizedBriefingItem] = Field(default_factory=list, max_length=40)
    rejected_sources: list[PersonalizedRejectedSource] = Field(default_factory=list)


class PersonalizedSourceDecision(BaseModel):
    """A compact personalized decision for one source."""

    model_config = ConfigDict(extra="forbid")

    source_key: str
    priority: str = Field(..., pattern=r"^(read|skim|reject)$")
    reason: str = Field(..., min_length=8, max_length=180)


class PersonalizedSelectionResult(BaseModel):
    """Compact preference decision output."""

    model_config = ConfigDict(extra="forbid")

    applied_preference_summary: str = Field(..., min_length=20, max_length=400)
    decisions: list[PersonalizedSourceDecision] = Field(..., min_length=1)


class LlmCallTimeoutError(RuntimeError):
    """Raised when a foreground LLM call exceeds the prototype timeout."""


@dataclass(frozen=True)
class ProseLens:
    """A selectable source subset rendered as one continuous briefing."""

    lens_id: str
    title: str
    deck: str
    sources: list[SourceItem]
    briefing: GeneratedUnreadBriefing
    warnings: list[str]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Generate dense unread briefing variants.")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--input-json", type=Path, default=DEFAULT_INPUT_JSON)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--model", default=OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC)
    parser.add_argument("--timeout-seconds", type=int, default=360)
    parser.add_argument(
        "--prose-llm",
        action="store_true",
        help="Use the LLM to write filtered/category prose lenses instead of deterministic prose.",
    )
    parser.add_argument(
        "--prose-window-size",
        type=int,
        default=8,
        help="Source window size for generated prose lenses.",
    )
    parser.add_argument(
        "--skip-repair-llm",
        action="store_true",
        help="Use deterministic missed-link repair chunks for generated prose lenses.",
    )
    parser.add_argument(
        "--personalization-prompt",
        default=DEFAULT_PERSONALIZATION_PROMPT,
        help="Reader preference prompt for the personalized variant.",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Render deterministic fallback variants from the source snapshot.",
    )
    parser.add_argument(
        "--skip-category-llm",
        action="store_true",
        help="Use deterministic category grouping but still run personalization LLM.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the variant generation."""
    args = parse_args(argv)
    payload = json.loads(args.input_json.read_text(encoding="utf-8"))
    sources = [SourceItem(**source) for source in payload["sources"]]
    output_dir = args.output_dir or args.input_json.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    category_prompt = build_category_prompt(sources=sources)
    personalization_prompt = build_personalization_prompt(
        sources=sources,
        preference_prompt=args.personalization_prompt,
    )
    (output_dir / "categories_prompt.md").write_text(category_prompt, encoding="utf-8")
    (output_dir / "personalized_prompt.md").write_text(
        personalization_prompt,
        encoding="utf-8",
    )

    usage: dict[str, int | None] | None = None
    generation_warnings: list[str] = []
    if args.skip_llm:
        category_output = build_fallback_category_briefing(sources)
        personalized_output = build_fallback_personalized_briefing(
            sources=sources,
            preference_prompt=args.personalization_prompt,
        )
    else:
        if args.skip_category_llm:
            category_output = build_fallback_category_briefing(sources)
            generation_warnings.append(
                "Category LLM generation skipped; used deterministic grouping."
            )
        else:
            try:
                category_result = run_structured_generation(
                    model_spec=args.model,
                    output_type=DenseCategoryBriefing,
                    system_prompt=(
                        "You produce dense, source-linked Newsly briefing layouts. Compress hard, "
                        "preserve useful breadth, and use only exact input source keys."
                    ),
                    prompt=category_prompt,
                    timeout_seconds=args.timeout_seconds,
                    label="category briefing variant",
                )
                category_output = category_result.output
                usage = merge_usage(usage, extract_usage_from_result(category_result))
            except Exception as exc:  # noqa: BLE001
                category_output = build_fallback_category_briefing(sources)
                generation_warnings.append(
                    f"Category LLM generation failed ({exc}); used deterministic grouping."
                )

        try:
            personalized_result = run_structured_generation(
                model_spec=args.model,
                output_type=PersonalizedSelectionResult,
                system_prompt=(
                    "You are a Newsly personalization editor. Apply the reader preference "
                    "prompt strictly and return compact per-source decisions using exact "
                    "input source keys."
                ),
                prompt=personalization_prompt,
                timeout_seconds=args.timeout_seconds,
                label="personalized source decisions",
            )
            personalized_output = build_personalized_briefing_from_selection(
                selection=personalized_result.output,
                sources=sources,
                preference_prompt=args.personalization_prompt,
            )
            usage = merge_usage(usage, extract_usage_from_result(personalized_result))
        except Exception as exc:  # noqa: BLE001
            personalized_output = build_fallback_personalized_briefing(
                sources=sources,
                preference_prompt=args.personalization_prompt,
            )
            generation_warnings.append(
                f"Personalization LLM generation failed ({exc}); used deterministic fallback."
            )

    source_map = {source.source_key: source for source in sources}
    category_output, category_warnings = normalize_category_output(
        category_output,
        source_map,
    )
    personalized_output, personalized_warnings = normalize_personalized_output(
        personalized_output,
        source_map,
    )
    category_warnings = generation_warnings + category_warnings
    personalized_warnings = generation_warnings + personalized_warnings

    category_lenses, category_prose_usage, category_prose_warnings = build_category_prose_lenses(
        category_output,
        sources=sources,
        model_spec=args.model,
        user_id=args.user_id,
        use_llm=args.prose_llm and not args.skip_llm,
        window_size=args.prose_window_size,
        timeout_seconds=args.timeout_seconds,
        skip_repair_llm=args.skip_repair_llm,
    )
    personalized_lens, personalized_prose_usage, personalized_prose_warnings = (
        build_personalized_prose_lens(
            personalized_output,
            sources=sources,
            model_spec=args.model,
            user_id=args.user_id,
            use_llm=args.prose_llm and not args.skip_llm,
            window_size=args.prose_window_size,
            timeout_seconds=args.timeout_seconds,
            skip_repair_llm=args.skip_repair_llm,
        )
    )
    usage = merge_usage(usage, category_prose_usage)
    usage = merge_usage(usage, personalized_prose_usage)
    category_warnings = category_warnings + category_prose_warnings
    personalized_warnings = personalized_warnings + personalized_prose_warnings

    category_payload = {
        "model": None if args.skip_llm else args.model,
        "source_count": len(sources),
        "usage": usage,
        "briefing": category_output.model_dump(mode="json"),
        "prose_lenses": [serialize_prose_lens(lens) for lens in category_lenses],
        "warnings": category_warnings,
    }
    personalized_payload = {
        "model": None if args.skip_llm else args.model,
        "source_count": len(sources),
        "usage": usage,
        "personalization_prompt": args.personalization_prompt,
        "briefing": personalized_output.model_dump(mode="json"),
        "prose_lens": serialize_prose_lens(personalized_lens),
        "warnings": personalized_warnings,
    }
    (output_dir / "categories.json").write_text(
        json.dumps(category_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "personalized.json").write_text(
        json.dumps(personalized_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "categories.html").write_text(
        render_category_html(
            category_output,
            category_lenses=category_lenses,
            sources=sources,
            model=None if args.skip_llm else args.model,
            warnings=category_warnings,
        ),
        encoding="utf-8",
    )
    (output_dir / "personalized.html").write_text(
        render_personalized_html(
            personalized_output,
            personalized_lens=personalized_lens,
            sources=sources,
            model=None if args.skip_llm else args.model,
            preference_prompt=args.personalization_prompt,
            warnings=personalized_warnings,
        ),
        encoding="utf-8",
    )
    (output_dir / "variants.html").write_text(
        render_variant_index(
            source_count=len(sources),
            model=None if args.skip_llm else args.model,
        ),
        encoding="utf-8",
    )

    print(f"Wrote category variant: {output_dir / 'categories.html'}")
    print(f"Wrote personalized variant: {output_dir / 'personalized.html'}")
    print(f"Wrote variant index: {output_dir / 'variants.html'}")
    if usage:
        print(f"LLM usage: {usage}")
    for warning in category_warnings + personalized_warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    return 0


@contextmanager
def llm_call_timeout(seconds: int, label: str):
    """Interrupt a long foreground LLM call."""
    if seconds <= 0:
        yield
        return

    def handle_timeout(_signum: int, _frame: Any) -> None:
        raise LlmCallTimeoutError(f"{label} timed out after {seconds}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def run_structured_generation(
    *,
    model_spec: str,
    output_type: type[BaseModel],
    system_prompt: str,
    prompt: str,
    timeout_seconds: int,
    label: str,
) -> Any:
    """Run a structured LLM call."""
    agent = get_basic_agent(model_spec, output_type, system_prompt)
    with llm_call_timeout(timeout_seconds, label):
        return agent.run_sync(prompt)


def build_category_prompt(*, sources: list[SourceItem]) -> str:
    """Build the category grouping prompt."""
    payload = {
        "source_count": len(sources),
        "sources": [source.to_selection_prompt_dict() for source in sources],
    }
    return (
        "Create a dense category-grouped Newsly briefing from the unread source set.\n\n"
        "What this is testing:\n"
        "- Whether category grouping can make the briefing faster than scanning the raw list.\n"
        "- Whether we can increase information density without losing source links.\n\n"
        "Rules:\n"
        "- Create 6-10 reader-meaningful categories. Categories should be areas a technical "
        "reader can choose between, not generic labels like Miscellaneous.\n"
        "- Use dense scan writing: short category summaries and compact item rows.\n"
        "- Prefer synthesis over narration. Do not write a long prose arc.\n"
        "- Each item may combine 1-3 closely related sources if that improves compression.\n"
        "- Keep unique oddities if they teach something, but put them in a category where "
        "their relevance is clear.\n"
        "- Use source_keys exactly as given. Do not invent source keys.\n"
        "- Try to cover every source once. Put weak leftovers in omitted_sources only when "
        "they truly add no reading value.\n"
        "- Every item should answer: what happened, why it matters, and which source(s) it "
        "comes from.\n\n"
        "Style:\n"
        "- item_title: title-like, but shorter than an article title.\n"
        "- compressed_takeaway: the highest-density useful statement, not setup.\n"
        "- why_it_matters: practical implication, pattern, risk, or reader reason.\n\n"
        "Input JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def build_personalization_prompt(
    *,
    sources: list[SourceItem],
    preference_prompt: str,
) -> str:
    """Build the personalization prompt."""
    payload = {
        "source_count": len(sources),
        "preference_prompt": preference_prompt,
        "sources": [source.to_selection_prompt_dict() for source in sources],
    }
    return (
        "Create a preference-filtered Newsly briefing.\n\n"
        "What this is testing:\n"
        "- Whether a reader preference prompt can reduce reading time better than a generic "
        "briefing.\n"
        "- Whether the kept items have higher information value for this specific reader.\n\n"
        "Reader preference prompt:\n"
        f"{preference_prompt}\n\n"
        "Rules:\n"
        "- Return compact decisions, not rewritten article summaries.\n"
        "- Use priority=read for the highest-signal material. Target 14-22 sources.\n"
        "- Use priority=skim for useful context that should not dominate. Target 10-20 "
        "sources.\n"
        "- Use priority=reject for genuinely lower-value or off-profile sources.\n"
        "- Return at most one decision per source.\n"
        "- Use exact source_keys from the input. Do not invent source keys.\n"
        "- reason should explain why this item matches, partially matches, or misses the "
        "preference prompt.\n\n"
        "Input JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def build_personalized_briefing_from_selection(
    *,
    selection: PersonalizedSelectionResult,
    sources: list[SourceItem],
    preference_prompt: str,
) -> PersonalizedBriefing:
    """Build renderable personalized output from compact LLM decisions."""
    source_map = {source.source_key: source for source in sources}
    read_items: list[PersonalizedBriefingItem] = []
    skim_items: list[PersonalizedBriefingItem] = []
    rejected_sources: list[PersonalizedRejectedSource] = []
    seen: set[str] = set()
    for decision in selection.decisions:
        source = source_map.get(decision.source_key)
        if not source or decision.source_key in seen:
            continue
        seen.add(decision.source_key)
        if decision.priority == "reject":
            rejected_sources.append(
                PersonalizedRejectedSource(
                    source_key=decision.source_key,
                    reason=decision.reason,
                )
            )
            continue

        item = PersonalizedBriefingItem(
            source_keys=[decision.source_key],
            priority=decision.priority,
            headline=trim_field(source.original_title, 100),
            value=trim_field(
                source.summary or next(iter(source.key_points), "Review this source."),
                220,
            ),
            preference_match=trim_field(decision.reason, 180),
        )
        if decision.priority == "read":
            read_items.append(item)
        else:
            skim_items.append(item)

    for source in sources:
        if source.source_key not in seen:
            rejected_sources.append(
                PersonalizedRejectedSource(
                    source_key=source.source_key,
                    reason="Not returned by the preference decision pass.",
                )
            )

    return PersonalizedBriefing(
        title="Preference-filtered unread scan",
        deck="A compact read/skim/filter view generated from an explicit preference prompt.",
        applied_preference_summary=selection.applied_preference_summary or preference_prompt[:400],
        read_items=read_items,
        skim_items=skim_items,
        rejected_sources=rejected_sources,
    )


def normalize_category_output(
    briefing: DenseCategoryBriefing,
    source_map: dict[str, SourceItem],
) -> tuple[DenseCategoryBriefing, list[str]]:
    """Drop unknown source keys and report coverage warnings."""
    warnings: list[str] = []
    seen: set[str] = set()
    categories: list[DenseCategory] = []
    unknown: set[str] = set()
    duplicate: set[str] = set()
    for category in briefing.categories:
        items: list[DenseCategoryItem] = []
        for item in category.items:
            valid_keys: list[str] = []
            for source_key in item.source_keys:
                if source_key not in source_map:
                    unknown.add(source_key)
                    continue
                if source_key in seen:
                    duplicate.add(source_key)
                seen.add(source_key)
                valid_keys.append(source_key)
            if valid_keys:
                items.append(item.model_copy(update={"source_keys": valid_keys}))
        if items:
            categories.append(category.model_copy(update={"items": items}))
    missing = sorted(set(source_map) - seen - set(briefing.omitted_sources))
    if unknown:
        warnings.append(f"Category variant ignored unknown sources: {sorted(unknown)}")
    if duplicate:
        warnings.append(f"Category variant reused sources: {sorted(duplicate)}")
    if missing:
        warnings.append(f"Category variant did not place {len(missing)} sources: {missing}")
    return briefing.model_copy(update={"categories": categories}), warnings


def normalize_personalized_output(
    briefing: PersonalizedBriefing,
    source_map: dict[str, SourceItem],
) -> tuple[PersonalizedBriefing, list[str]]:
    """Drop unknown source keys from personalized output."""
    warnings: list[str] = []
    seen: set[str] = set()
    unknown: set[str] = set()
    duplicate: set[str] = set()

    def normalize_items(
        items: list[PersonalizedBriefingItem],
    ) -> list[PersonalizedBriefingItem]:
        normalized: list[PersonalizedBriefingItem] = []
        for item in items:
            valid_keys: list[str] = []
            for source_key in item.source_keys:
                if source_key not in source_map:
                    unknown.add(source_key)
                    continue
                if source_key in seen:
                    duplicate.add(source_key)
                seen.add(source_key)
                valid_keys.append(source_key)
            if valid_keys:
                normalized.append(item.model_copy(update={"source_keys": valid_keys}))
        return normalized

    read_items = normalize_items(briefing.read_items)
    skim_items = normalize_items(briefing.skim_items)
    rejected = [item for item in briefing.rejected_sources if item.source_key in source_map]
    rejected_keys = {item.source_key for item in rejected}
    missing = sorted(set(source_map) - seen - rejected_keys)
    if unknown:
        warnings.append(f"Personalized variant ignored unknown sources: {sorted(unknown)}")
    if duplicate:
        warnings.append(f"Personalized variant reused sources: {sorted(duplicate)}")
    if missing:
        warnings.append(
            f"Personalized variant neither kept nor rejected {len(missing)} sources: {missing}"
        )
    return (
        briefing.model_copy(
            update={
                "read_items": read_items,
                "skim_items": skim_items,
                "rejected_sources": rejected,
            }
        ),
        warnings,
    )


def build_fallback_category_briefing(sources: list[SourceItem]) -> DenseCategoryBriefing:
    """Build a deterministic category fallback."""
    grouped: dict[str, list[SourceItem]] = {}
    for source in sources:
        category = fallback_category_for_source(source)
        grouped.setdefault(category, []).append(source)
    categories = [
        DenseCategory(
            category_id=slugify(title),
            title=title,
            scan_summary=f"{len(items)} sources in this category filter.",
            items=[
                DenseCategoryItem(
                    item_title=trim_field(source.original_title, 100),
                    compressed_takeaway=trim_field(
                        source.summary
                        or next(iter(source.key_points), "Review this source for details."),
                        220,
                    ),
                    why_it_matters=trim_field(
                        next(iter(source.key_points), source.kind),
                        180,
                    ),
                    source_keys=[source.source_key],
                )
                for source in items
            ],
        )
        for title, items in grouped.items()
    ]
    return DenseCategoryBriefing(
        title="Category-filtered briefings",
        deck="Choose a category filter to read that source subset as one continuous briefing.",
        categories=categories,
        omitted_sources=[],
    )


def build_fallback_personalized_briefing(
    *,
    sources: list[SourceItem],
    preference_prompt: str,
) -> PersonalizedBriefing:
    """Build a deterministic personalized fallback from preference-shaped scoring."""
    scored_sources = [(source, *score_source_for_personalization(source)) for source in sources]
    scored_sources.sort(
        key=lambda item: (item[1], item[0].sort_timestamp or "", item[0].source_key),
        reverse=True,
    )
    read_scored = scored_sources[: min(20, len(scored_sources))]
    skim_scored = scored_sources[len(read_scored) : min(len(scored_sources), len(read_scored) + 20)]
    rejected_scored = scored_sources[len(read_scored) + len(skim_scored) :]
    return PersonalizedBriefing(
        title="Preference-filtered unread briefing",
        deck="Deterministic preference fallback using the current preference prompt.",
        applied_preference_summary=trim_field(preference_prompt, 400),
        read_items=[
            fallback_personalized_item(source, "read", reasons)
            for source, _score, reasons in read_scored
        ],
        skim_items=[
            fallback_personalized_item(source, "skim", reasons)
            for source, _score, reasons in skim_scored
        ],
        rejected_sources=[
            PersonalizedRejectedSource(
                source_key=source.source_key,
                reason=(
                    "Lower preference score in deterministic fallback: "
                    f"{'; '.join(reasons[:2]) if reasons else 'weaker profile match'}."
                ),
            )
            for source, _score, reasons in rejected_scored
        ],
    )


def fallback_personalized_item(
    source: SourceItem,
    priority: str,
    reasons: list[str] | None = None,
) -> PersonalizedBriefingItem:
    """Return one deterministic personalized item."""
    reason = "; ".join((reasons or [])[:3]) or next(iter(source.key_points), source.kind)
    return PersonalizedBriefingItem(
        source_keys=[source.source_key],
        priority=priority,
        headline=trim_field(source.original_title, 100),
        value=trim_field(
            source.summary or next(iter(source.key_points), "Review this source for details."),
            220,
        ),
        preference_match=trim_field(reason, 180),
    )


def score_source_for_personalization(source: SourceItem) -> tuple[int, list[str]]:
    """Score a source for the built-in technical product-builder preference fallback."""
    text = " ".join(
        [
            source.original_title,
            source.summary or "",
            " ".join(source.key_points),
            source.kind,
            source.source_name or "",
        ]
    ).lower()
    score = 0
    reasons: list[str] = []
    positive_groups: list[tuple[int, str, tuple[str, ...]]] = [
        (
            8,
            "AI agent or coding workflow",
            (
                "agent",
                "agents",
                "codex",
                "coding",
                "developer",
                "devrel",
                "skill",
                "emacs",
                "lisp",
                "dbase",
                "business-logic",
            ),
        ),
        (
            7,
            "model capability or evaluation signal",
            (
                "gpt",
                "claude",
                "gemini",
                "deepseek",
                "glm",
                "model",
                "openai",
                "anthropic",
                "eval",
                "benchmark",
                "rag",
                "image",
                "speech",
            ),
        ),
        (
            6,
            "AI infrastructure or compute market",
            (
                "inference",
                "openrouter",
                "fireworks",
                "baseten",
                "gpu",
                "nvidia",
                "memory",
                "micron",
                "kioxia",
                "compute",
                "token",
                "latency",
                "cost",
            ),
        ),
        (
            5,
            "security, trust, or governance",
            (
                "security",
                "trust",
                "prompt injection",
                "captcha",
                "vulnerability",
                "open source",
                "policy",
                "oversight",
                "regulation",
                "antitrust",
            ),
        ),
        (
            4,
            "platform or business-model strategy",
            (
                "platform",
                "strategy",
                "business",
                "pricing",
                "subscription",
                "startup",
                "valuation",
                "sequoia",
                "enterprise",
                "market",
            ),
        ),
        (
            3,
            "weird technical artifact",
            (
                "barcode",
                "wasm",
                "rust",
                "webgpu",
                "ebpf",
                "sockmap",
                "matching",
                "complexity class",
            ),
        ),
    ]
    negative_groups: list[tuple[int, str, tuple[str, ...]]] = [
        (
            -5,
            "mostly lifestyle or generic culture",
            (
                "restaurant",
                "teen",
                "lawns",
                "mozart",
                "swatch",
                "households",
                "mid-career",
                "white-collar",
                "social media",
            ),
        ),
        (
            -3,
            "thin rumor or blocked context",
            (
                "rumor",
                "blocked",
                "cannot be verified",
                "body is blocked",
            ),
        ),
    ]

    for weight, reason, tokens in positive_groups:
        if any(token in text for token in tokens):
            score += weight
            reasons.append(reason)
    for weight, reason, tokens in negative_groups:
        if any(token in text for token in tokens):
            score += weight
            reasons.append(reason)
    if source.kind.startswith("long_"):
        score += 2
        reasons.append("long-form depth")
    if source.summary and len(source.summary) > 180:
        score += 1
    return score, reasons


def build_category_prose_lenses(
    briefing: DenseCategoryBriefing,
    *,
    sources: list[SourceItem],
    model_spec: str,
    user_id: int,
    use_llm: bool,
    window_size: int,
    timeout_seconds: int,
    skip_repair_llm: bool,
) -> tuple[list[ProseLens], dict[str, int | None] | None, list[str]]:
    """Build one prose briefing lens per category."""
    source_by_key = {source.source_key: source for source in sources}
    usage: dict[str, int | None] | None = None
    warnings: list[str] = []
    lenses: list[ProseLens] = []
    for category in briefing.categories:
        lens_sources = ordered_sources_for_keys(
            source_keys=source_keys_for_category(category),
            source_by_key=source_by_key,
        )
        if not lens_sources:
            warnings.append(f"Category lens {category.title!r} has no valid sources.")
            continue
        title = f"{category.title} briefing"
        deck = category.scan_summary
        lens, lens_usage, lens_warnings = build_prose_lens(
            lens_id=category.category_id,
            title=title,
            deck=deck,
            sources=lens_sources,
            model_spec=model_spec,
            user_id=user_id,
            use_llm=use_llm,
            window_size=window_size,
            timeout_seconds=timeout_seconds,
            skip_repair_llm=skip_repair_llm,
        )
        usage = merge_usage(usage, lens_usage)
        warnings.extend(f"{category.title}: {warning}" for warning in lens_warnings)
        lenses.append(lens)
    return lenses, usage, warnings


def build_personalized_prose_lens(
    briefing: PersonalizedBriefing,
    *,
    sources: list[SourceItem],
    model_spec: str,
    user_id: int,
    use_llm: bool,
    window_size: int,
    timeout_seconds: int,
    skip_repair_llm: bool,
) -> tuple[ProseLens, dict[str, int | None] | None, list[str]]:
    """Build one prose briefing from personalized read-priority sources."""
    source_by_key = {source.source_key: source for source in sources}
    read_keys = [key for item in briefing.read_items for key in item.source_keys]
    lens_sources = ordered_sources_for_keys(source_keys=read_keys, source_by_key=source_by_key)
    warnings: list[str] = []
    if not lens_sources:
        fallback_keys = [key for item in briefing.skim_items for key in item.source_keys]
        lens_sources = ordered_sources_for_keys(
            source_keys=fallback_keys,
            source_by_key=source_by_key,
        )
        warnings.append("Personalized read set was empty; used skim sources.")
    if not lens_sources:
        lens_sources = sources[: min(20, len(sources))]
        warnings.append("Personalized source set was empty; used recency fallback.")

    lens, usage, lens_warnings = build_prose_lens(
        lens_id="personalized",
        title="Personalized briefing",
        deck=briefing.applied_preference_summary,
        sources=lens_sources,
        model_spec=model_spec,
        user_id=user_id,
        use_llm=use_llm,
        window_size=window_size,
        timeout_seconds=timeout_seconds,
        skip_repair_llm=skip_repair_llm,
    )
    warnings.extend(lens_warnings)
    return lens, usage, warnings


def build_prose_lens(
    *,
    lens_id: str,
    title: str,
    deck: str,
    sources: list[SourceItem],
    model_spec: str,
    user_id: int,
    use_llm: bool,
    window_size: int,
    timeout_seconds: int,
    skip_repair_llm: bool,
) -> tuple[ProseLens, dict[str, int | None] | None, list[str]]:
    """Build one generated or deterministic prose lens."""
    warnings: list[str] = []
    usage: dict[str, int | None] | None = None
    if use_llm:
        try:
            briefing, usage = run_windowed_generation(
                model_spec=model_spec,
                user_id=user_id,
                sources=sources,
                window_size=max(1, window_size),
                timeout_seconds=timeout_seconds,
                skip_repair_llm=skip_repair_llm,
            )
            briefing = briefing.model_copy(
                update={
                    "title": title,
                    "deck": deck,
                    "through_line": (
                        "A filtered source subset rendered as one continuous briefing."
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            briefing = build_deterministic_prose_briefing(
                title=title,
                deck=deck,
                sources=sources,
            )
            warnings.append(
                f"Filtered prose LLM generation failed ({exc}); used deterministic prose."
            )
    else:
        briefing = build_deterministic_prose_briefing(
            title=title,
            deck=deck,
            sources=sources,
        )

    briefing, normalization_warnings = normalize_generated_briefing(briefing)
    warnings.extend(normalization_warnings)
    warnings.extend(validate_generated_sources(briefing, sources))
    briefing = prefix_lens_insight_ids(briefing, lens_id)
    return (
        ProseLens(
            lens_id=lens_id,
            title=title,
            deck=deck,
            sources=sources,
            briefing=briefing,
            warnings=warnings,
        ),
        usage,
        warnings,
    )


def build_deterministic_prose_briefing(
    *,
    title: str,
    deck: str,
    sources: list[SourceItem],
) -> GeneratedUnreadBriefing:
    """Build continuous source-linked prose without a foreground LLM call."""
    chunk_size = 7
    chunks = [
        build_fallback_repair_chunk(
            sources=sources[start : start + chunk_size],
            chunk_index=(start // chunk_size) + 1,
        )
        for start in range(0, len(sources), chunk_size)
    ]
    return GeneratedUnreadBriefing(
        title=title,
        deck=deck,
        through_line="Filtered source subset rendered as continuous source-linked prose.",
        chunks=chunks,
        omitted_sources=[],
    )


def prefix_lens_insight_ids(
    briefing: GeneratedUnreadBriefing,
    lens_id: str,
) -> GeneratedUnreadBriefing:
    """Keep insight ids unique across several rendered lenses on one page."""
    prefix = re.sub(r"[^A-Za-z0-9_]+", "_", lens_id)[:18].strip("_") or "lens"
    next_index = 1
    chunks = []
    for chunk in briefing.chunks:
        markdown = chunk.markdown
        insights = []
        for insight in chunk.insights:
            new_id = f"{prefix}_{next_index}"
            next_index += 1
            markdown = re.sub(
                rf"\{{\{{insight:{re.escape(insight.insight_id)}\}}\}}",
                f"{{{{insight:{new_id}}}}}",
                markdown,
            )
            insights.append(insight.model_copy(update={"insight_id": new_id}))
        chunks.append(chunk.model_copy(update={"markdown": markdown, "insights": insights}))
    return briefing.model_copy(update={"chunks": chunks})


def source_keys_for_category(category: DenseCategory) -> list[str]:
    """Return category source keys in item order without duplicates."""
    keys: list[str] = []
    seen: set[str] = set()
    for item in category.items:
        for source_key in item.source_keys:
            if source_key in seen:
                continue
            seen.add(source_key)
            keys.append(source_key)
    return keys


def ordered_sources_for_keys(
    *,
    source_keys: list[str],
    source_by_key: dict[str, SourceItem],
) -> list[SourceItem]:
    """Resolve source keys while preserving the selector's order."""
    return [source_by_key[key] for key in source_keys if key in source_by_key]


def serialize_prose_lens(lens: ProseLens) -> dict[str, Any]:
    """Serialize a prose lens for JSON artifacts."""
    return {
        "lens_id": lens.lens_id,
        "title": lens.title,
        "deck": lens.deck,
        "source_count": len(lens.sources),
        "source_keys": [source.source_key for source in lens.sources],
        "briefing": lens.briefing.model_dump(mode="json"),
        "warnings": lens.warnings,
    }


def fallback_category_for_source(source: SourceItem) -> str:
    """Return a rough deterministic category."""
    text = f"{source.original_title} {source.summary or ''}".lower()

    if any(
        token in text
        for token in (
            "security",
            "vulnerability",
            "captcha",
            "prompt injection",
            "sponsor",
            "akrites",
            "trust",
        )
    ):
        return "Security and Trust"

    if any(
        token in text
        for token in (
            "lisp",
            "dbase",
            "emacs",
            "developer",
            "github",
            "database",
            "runtime",
            "rust",
            "ebpf",
            "sockmap",
            "barcode",
            "business-logic",
            "devrel",
            "codex",
            "skill",
        )
    ):
        return "Developer Tools"

    if any(
        token in text
        for token in (
            "space",
            "chip",
            "nvidia",
            "compute",
            "starlink",
            "memory",
            "micron",
            "kioxia",
            "token",
            "infra",
            "baseten",
            "fireworks",
            "openrouter",
            "cann",
            "huawei",
            "cost",
            "price",
        )
    ):
        return "Infrastructure and Compute"

    if any(
        token in text
        for token in (
            "policy",
            "court",
            "government",
            "pentagon",
            "mi ca",
            "mica",
            "eu ",
            "italy",
            "ban",
            "china",
            "us-china",
            "oversight",
            "extinction",
        )
    ):
        return "Policy and Power"

    if any(
        token in text
        for token in (
            "protein",
            "drug delivery",
            "medical",
            "organs",
            "cancer",
            "lab",
            "physical ai",
            "microbubbles",
            "esmf",
            "noetik",
        )
    ):
        return "Science and Health"

    if any(
        token in text
        for token in (
            "restaurant",
            "teen",
            "social media",
            "mozart",
            "lawns",
            "master",
            "mid-career",
            "white-collar",
            "zen",
            "prometheus",
            "households",
            "sweeney",
            "swatch",
        )
    ):
        return "Culture and Work"

    if any(
        token in text
        for token in (
            "agent",
            "agi",
            "model",
            "gemini",
            "claude",
            "gpt",
            "openai",
            "deepseek",
            "glm",
            "imagegen",
            "eval",
            "rag",
            "outputmaxxing",
        )
    ):
        return "AI Models and Agents"

    if any(
        token in text
        for token in (
            "selling",
            "business",
            "startup",
            "capital",
            "sequoia",
            "valuation",
            "prestige",
        )
    ):
        return "Business and Strategy"

    return "Oddities and Context"


def trim_field(value: str, max_chars: int) -> str:
    """Trim a structured-output field without producing an empty string."""
    compact = " ".join(str(value or "").split())
    if len(compact) <= max_chars:
        return compact or "Review this source."
    trimmed = compact[: max(0, max_chars - 1)].rstrip()
    boundary = trimmed.rfind(" ")
    if boundary >= max_chars // 2:
        trimmed = trimmed[:boundary].rstrip()
    return f"{trimmed}…"


def render_category_html(
    briefing: DenseCategoryBriefing,
    *,
    category_lenses: list[ProseLens],
    sources: list[SourceItem],
    model: str | None,
    warnings: list[str],
) -> str:
    """Render category filters as prose briefing lenses."""
    return render_prose_lens_page(
        title="Category-filtered briefings",
        deck=briefing.deck,
        lenses=category_lenses,
        all_sources=sources,
        model=model,
        active_page="categories",
        warnings=warnings,
    )


def render_personalized_html(
    briefing: PersonalizedBriefing,
    *,
    personalized_lens: ProseLens,
    sources: list[SourceItem],
    model: str | None,
    preference_prompt: str,
    warnings: list[str],
) -> str:
    """Render the personalized prototype as one prose briefing."""
    del briefing
    prompt_details = f"<details><summary>Preference prompt</summary><p>{html.escape(preference_prompt)}</p></details>"
    return render_prose_lens_page(
        title="Personalized briefing",
        deck=personalized_lens.deck,
        lenses=[personalized_lens],
        all_sources=sources,
        model=model,
        active_page="personalized",
        warnings=warnings,
        extra_header_html=prompt_details,
    )


def render_variant_index(*, source_count: int, model: str | None) -> str:
    """Render a small index page for both variants."""
    body = (
        "<header><h1>Unread Briefing Variants</h1>"
        f"<p>{source_count} current unread sources · {html.escape(str(model))}</p></header>"
        '<section class="category-section">'
        '<div class="dense-list">'
        '<article class="dense-row"><h3>Category-filtered prose</h3>'
        "<p>Choose a category, then read only that source subset as one continuous briefing.</p>"
        '<p><a href="categories.html">Open categories</a></p></article>'
        '<article class="dense-row"><h3>Personalized prose</h3>'
        "<p>Applies the preference prompt, then writes the selected articles as one briefing.</p>"
        '<p><a href="personalized.html">Open personalized</a></p></article>'
        "</div></section>"
    )
    return render_html_shell(
        title="Unread Briefing Variants",
        body=body,
        sources=[],
        active_page="index",
    )


def render_prose_lens_page(
    *,
    title: str,
    deck: str,
    lenses: list[ProseLens],
    all_sources: list[SourceItem],
    model: str | None,
    active_page: str,
    warnings: list[str],
    extra_header_html: str = "",
) -> str:
    """Render one or more selectable prose briefings."""
    source_json = json.dumps(
        {source.source_key: source.to_manifest_dict() for source in all_sources},
        ensure_ascii=False,
    ).replace("</", "<\\/")
    insight_json = json.dumps(
        {
            insight.insight_id: insight.model_dump(mode="json")
            for lens in lenses
            for chunk in lens.briefing.chunks
            for insight in chunk.insights
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    total_sources = len({source.source_key for lens in lenses for source in lens.sources})
    nav = render_lens_nav(lenses)
    panels = "\n".join(
        render_lens_panel(lens, hidden=index > 0) for index, lens in enumerate(lenses)
    )
    first_lens = lenses[0] if lenses else None
    active_label = (
        f"{first_lens.title} · {len(first_lens.sources)} sources" if first_lens else "No sources"
    )
    body = (
        "<header>"
        f"<h1>{html.escape(title)}</h1>"
        f"<p>{html.escape(deck)}</p>"
        f'<p class="meta">{total_sources} selected links from {len(all_sources)} sources · {html.escape(str(model))}</p>'
        f"{extra_header_html}"
        f"{nav}"
        "</header>"
        f'<div class="active-lens-title" id="active-lens-title">{html.escape(active_label)}</div>'
        f"{panels}"
        f"{render_warning_block(warnings)}"
    )
    return render_prose_html_shell(
        title=title,
        body=body,
        source_json=source_json,
        insight_json=insight_json,
        active_page=active_page,
    )


def render_lens_nav(lenses: list[ProseLens]) -> str:
    """Render category/filter controls."""
    if len(lenses) <= 1:
        return ""
    buttons = []
    for index, lens in enumerate(lenses):
        active = " active" if index == 0 else ""
        buttons.append(
            '<button type="button" '
            f'class="lens-button{active}" '
            f'data-lens-id="{html.escape(lens.lens_id)}" '
            f'data-lens-label="{html.escape(lens.title)} · {len(lens.sources)} sources">'
            f"{html.escape(lens.title.replace(' briefing', ''))}"
            f"<span>{len(lens.sources)}</span>"
            "</button>"
        )
    return f'<nav class="lens-nav" aria-label="Briefing filters">{"".join(buttons)}</nav>'


def render_lens_panel(lens: ProseLens, *, hidden: bool) -> str:
    """Render one continuous prose panel."""
    chunks_html = "\n".join(render_chunk_html(chunk) for chunk in lens.briefing.chunks)
    hidden_attr = " hidden" if hidden else ""
    source_keys = ",".join(source.source_key for source in lens.sources)
    return (
        f'<article class="lens-panel" id="lens-{html.escape(lens.lens_id)}" '
        f'data-lens-id="{html.escape(lens.lens_id)}" '
        f'data-source-count="{len(lens.sources)}" '
        f'data-source-keys="{html.escape(source_keys)}"{hidden_attr}>'
        f"{chunks_html}"
        "</article>"
    )


def render_prose_html_shell(
    *,
    title: str,
    body: str,
    source_json: str,
    insight_json: str,
    active_page: str,
) -> str:
    """Render shared shell for prose-lens pages."""
    template = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>@@TITLE@@</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #fbfbfa;
      --ink: #1f1f1d;
      --muted: #6b6a66;
      --line: #d9d8d4;
      --panel: #ffffff;
      --accent: #2f6f4e;
      --soft: #f1f0eb;
      --insight-bg: rgba(194, 136, 31, .16);
      --insight-active: rgba(194, 136, 31, .28);
      --insight-line: #9c6b1f;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #171817;
        --ink: #f1f0ec;
        --muted: #aaa8a0;
        --line: #383a36;
        --panel: #222420;
        --accent: #8cc7a2;
        --soft: #20221f;
        --insight-bg: rgba(207, 166, 83, .18);
        --insight-active: rgba(207, 166, 83, .30);
        --insight-line: #cfa653;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 17px/1.6 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
    }
    main {
      width: min(840px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 34px 0 96px;
    }
    header {
      border-bottom: 1px solid var(--line);
      margin-bottom: 18px;
      padding-bottom: 14px;
    }
    h1 {
      font-size: 1.45rem;
      line-height: 1.12;
      letter-spacing: 0;
      margin: 0 0 8px;
      max-width: 680px;
    }
    p { margin: 0 0 1rem; }
    header p,
    .meta,
    .active-lens-title {
      color: var(--muted);
    }
    .meta {
      font-size: .86rem;
      margin-bottom: 0;
    }
    a {
      color: var(--accent);
      text-underline-offset: 3px;
    }
    details {
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-top: 12px;
      padding: 8px 10px;
      color: var(--muted);
      font-size: .9rem;
    }
    details summary { cursor: pointer; color: var(--ink); }
    .lens-nav {
      display: flex;
      gap: 8px;
      overflow-x: auto;
      padding-top: 12px;
      scrollbar-width: none;
    }
    .lens-button {
      appearance: none;
      background: transparent;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--ink);
      cursor: pointer;
      flex: 0 0 auto;
      font: inherit;
      font-size: .9rem;
      line-height: 1.15;
      padding: 7px 9px;
    }
    .lens-button.active {
      background: var(--soft);
      border-color: var(--accent);
    }
    .lens-button span {
      color: var(--muted);
      margin-left: 5px;
    }
    .active-lens-title {
      font-size: .88rem;
      margin: 0 0 14px;
    }
    .lens-panel[hidden] { display: none; }
    .chunk {
      margin: 0;
      padding: 0;
    }
    .chunk p { margin: 0 0 1rem; }
    a.source-link {
      color: var(--accent);
      cursor: pointer;
      text-decoration-thickness: 1px;
      text-underline-offset: 3px;
    }
    a.source-link.seen { text-decoration-style: dashed; }
    .semantic-hit {
      background: var(--insight-bg);
      box-shadow: inset 0 -1px var(--insight-line);
      cursor: pointer;
    }
    .semantic-hit.active {
      background: var(--insight-active);
      outline: 1px solid var(--insight-line);
      outline-offset: 2px;
    }
    .semantic-hit:focus-visible {
      outline: 2px solid var(--insight-line);
      outline-offset: 2px;
    }
    body.sheet-open { overflow: hidden; }
    .sheet-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,.30);
      opacity: 0;
      transition: opacity 160ms ease;
      z-index: 20;
    }
    .sheet-backdrop.open { opacity: 1; }
    .bottom-sheet {
      --sheet-drag-y: 0px;
      position: fixed;
      left: 50%;
      bottom: 0;
      width: min(720px, 100vw);
      height: min(76vh, 720px);
      background: var(--panel);
      border: 1px solid var(--line);
      border-bottom: 0;
      border-radius: 10px 10px 0 0;
      transform: translate(-50%, calc(100% + 12px));
      transition: transform 180ms ease;
      z-index: 30;
      display: flex;
      flex-direction: column;
      box-shadow: 0 -8px 24px rgba(0,0,0,.18);
    }
    .bottom-sheet.open {
      transform: translate(-50%, var(--sheet-drag-y));
    }
    .bottom-sheet.dragging { transition: none; }
    .sheet-top {
      flex: 0 0 auto;
      padding: 8px 16px 10px;
      border-bottom: 1px solid var(--line);
      touch-action: none;
      cursor: grab;
    }
    .sheet-grabber {
      width: 38px;
      height: 4px;
      border-radius: 2px;
      background: var(--line);
      margin: 0 auto 8px;
    }
    .sheet-actions {
      display: flex;
      justify-content: flex-end;
    }
    .sheet-close {
      appearance: none;
      background: transparent;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--ink);
      cursor: pointer;
      font: inherit;
      font-size: .88rem;
      line-height: 1;
      padding: 7px 10px;
    }
    .sheet-content {
      overflow: auto;
      padding: 18px 18px 28px;
      -webkit-overflow-scrolling: touch;
    }
    .sheet-title {
      margin: 0 0 8px;
      font-size: 1.08rem;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }
    .sheet-meta {
      color: var(--muted);
      font-size: .86rem;
      line-height: 1.4;
      margin: 0 0 12px;
    }
    .sheet-content p { margin: 0 0 12px; }
    .sheet-content ul { margin: 8px 0 14px 20px; padding: 0; }
    .sheet-content li { margin-bottom: 5px; }
    .insight-source-list {
      color: var(--muted);
      font-size: .88rem;
      margin-top: 14px;
    }
    .status {
      position: fixed;
      right: 14px;
      bottom: 14px;
      width: min(260px, calc(100vw - 28px));
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      color: var(--muted);
      font: .8rem/1.35 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
      box-shadow: 0 12px 30px rgba(0,0,0,.12);
    }
    .status strong { color: var(--ink); }
    .diagnostics {
      border-top: 1px solid var(--line);
      color: var(--muted);
      margin-top: 34px;
      padding-top: 18px;
      font-size: .92rem;
    }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  </style>
</head>
<body data-page="@@ACTIVE_PAGE@@">
  <main>@@BODY@@</main>
  <aside class="status">
    <strong>Seen</strong>
    <div id="seen-count">0 sources seen</div>
  </aside>
  <div class="sheet-backdrop" id="sheet-backdrop" hidden></div>
  <aside class="bottom-sheet" id="bottom-sheet" aria-hidden="true" role="dialog" aria-modal="true">
    <div class="sheet-top" id="sheet-drag-handle">
      <div class="sheet-grabber" aria-hidden="true"></div>
      <div class="sheet-actions">
        <button class="sheet-close" id="sheet-close" type="button">Close</button>
      </div>
    </div>
    <div class="sheet-content" id="sheet-content"></div>
  </aside>
  <script>
    const SOURCE_MAP = @@SOURCE_JSON@@;
    const INSIGHT_MAP = @@INSIGHT_JSON@@;
    const seen = new Set();
    const sheet = document.getElementById("bottom-sheet");
    const sheetContent = document.getElementById("sheet-content");
    const sheetBackdrop = document.getElementById("sheet-backdrop");
    const sheetClose = document.getElementById("sheet-close");
    const sheetDragHandle = document.getElementById("sheet-drag-handle");
    const activeLensTitle = document.getElementById("active-lens-title");
    let activeSemanticNode = null;
    let dragStartY = null;
    let dragPointerId = null;
    let dragDeltaY = 0;

    function escapeHtml(value) {
      return String(value || "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\\"": "&quot;",
        "'": "&#39;"
      }[char]));
    }
    function escapeAttr(value) {
      return escapeHtml(value).replace(/`/g, "&#96;");
    }
    function keyPointsList(source) {
      const points = source.key_points || [];
      if (!points.length) return "";
      return `<ul>${points.map((point) => `<li>${escapeHtml(point)}</li>`).join("")}</ul>`;
    }
    function sourceSheetContent(sourceKey) {
      const source = SOURCE_MAP[sourceKey];
      if (!source) return null;
      const external = source.url ? `<p><a href="${escapeAttr(source.url)}" target="_blank" rel="noreferrer">Open original</a></p>` : "";
      const summary = source.summary ? `<p>${escapeHtml(source.summary)}</p>` : "";
      return `<div class="source-detail" data-sheet-for="${escapeAttr(sourceKey)}">
        <h2 class="sheet-title">${escapeHtml(source.original_title)}</h2>
        <div class="sheet-meta">${escapeHtml(source.source_name || source.kind)} · ${escapeHtml(source.published_at || "undated")} · <code>${escapeHtml(sourceKey)}</code></div>
        ${summary}
        ${keyPointsList(source)}
        ${external}
      </div>`;
    }
    function insightSheetContent(insightId) {
      const insight = INSIGHT_MAP[insightId];
      if (!insight) return null;
      const sources = (insight.source_keys || [])
        .map((sourceKey) => SOURCE_MAP[sourceKey]?.original_title || sourceKey)
        .filter(Boolean);
      const sourceLine = sources.length
        ? `<div class="insight-source-list">Sources: ${sources.map(escapeHtml).join("; ")}</div>`
        : "";
      const questions = insight.follow_up_questions || [];
      const questionList = questions.length
        ? `<ul>${questions.map((question) => `<li>${escapeHtml(question)}</li>`).join("")}</ul>`
        : "";
      return `<div class="insight-detail" data-sheet-for="${escapeAttr(insightId)}">
        <h2 class="sheet-title">${escapeHtml(insight.title || "Learn more")}</h2>
        <p>${escapeHtml(insight.learn_more || "")}</p>
        ${sourceLine}
        ${questionList}
      </div>`;
    }
    function openSheet(contentHtml) {
      if (!contentHtml) return;
      sheetContent.innerHTML = contentHtml;
      sheet.style.setProperty("--sheet-drag-y", "0px");
      sheetBackdrop.hidden = false;
      requestAnimationFrame(() => {
        sheetBackdrop.classList.add("open");
        sheet.classList.add("open");
      });
      sheet.setAttribute("aria-hidden", "false");
      document.body.classList.add("sheet-open");
    }
    function clearActiveInsight() {
      if (activeSemanticNode) activeSemanticNode.classList.remove("active");
      activeSemanticNode = null;
      document.querySelectorAll(".semantic-hit.active").forEach((node) => node.classList.remove("active"));
    }
    function closeSheet() {
      sheetBackdrop.classList.remove("open");
      sheet.classList.remove("open");
      sheet.classList.remove("dragging");
      sheet.setAttribute("aria-hidden", "true");
      document.body.classList.remove("sheet-open");
      sheet.style.setProperty("--sheet-drag-y", "0px");
      clearActiveInsight();
      window.setTimeout(() => {
        if (!sheet.classList.contains("open")) {
          sheetBackdrop.hidden = true;
          sheetContent.innerHTML = "";
        }
      }, 190);
    }
    function activePanel() {
      return document.querySelector(".lens-panel:not([hidden])");
    }
    function updateSeen() {
      const panel = activePanel();
      if (!panel) return;
      panel.querySelectorAll("a.source-link").forEach((link) => {
        const rect = link.getBoundingClientRect();
        if (rect.bottom < window.innerHeight * 0.18) {
          seen.add(`${panel.dataset.lensId}:${link.dataset.sourceKey}`);
          link.classList.add("seen");
        }
      });
      const seenForPanel = Array.from(seen).filter((key) => key.startsWith(`${panel.dataset.lensId}:`)).length;
      document.getElementById("seen-count").textContent = `${seenForPanel} of ${panel.dataset.sourceCount} sources seen`;
    }
    function setLens(lensId, updateHash) {
      const target = Array.from(document.querySelectorAll(".lens-panel"))
        .find((panel) => panel.dataset.lensId === lensId);
      if (!target) return;
      document.querySelectorAll(".lens-panel").forEach((panel) => { panel.hidden = panel !== target; });
      document.querySelectorAll(".lens-button").forEach((button) => {
        const isActive = button.dataset.lensId === lensId;
        button.classList.toggle("active", isActive);
        if (isActive && activeLensTitle) activeLensTitle.textContent = button.dataset.lensLabel || "";
      });
      clearActiveInsight();
      updateSeen();
      if (updateHash) history.replaceState(null, "", `#${lensId}`);
    }
    document.addEventListener("click", (event) => {
      const lensButton = event.target.closest(".lens-button");
      if (lensButton) {
        setLens(lensButton.dataset.lensId, true);
        window.scrollTo(0, 0);
        return;
      }
      const link = event.target.closest("a.source-link");
      if (link) {
        event.preventDefault();
        clearActiveInsight();
        openSheet(sourceSheetContent(link.dataset.sourceKey));
        return;
      }
      const insightTarget = event.target.closest(".semantic-hit");
      if (!insightTarget) return;
      event.preventDefault();
      openInsight(insightTarget);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && sheet.classList.contains("open")) {
        closeSheet();
        return;
      }
      if (event.key !== "Enter" && event.key !== " ") return;
      const insightTarget = event.target.closest(".semantic-hit");
      if (!insightTarget || event.target.closest("a.source-link")) return;
      event.preventDefault();
      openInsight(insightTarget);
    });
    function openInsight(target) {
      const insightId = target.dataset.insightId;
      if (!insightId) return;
      const content = insightSheetContent(insightId);
      if (!content) return;
      clearActiveInsight();
      target.classList.add("active");
      activeSemanticNode = target;
      openSheet(content);
    }
    sheetBackdrop.addEventListener("click", closeSheet);
    sheetClose.addEventListener("click", closeSheet);
    sheetDragHandle.addEventListener("pointerdown", (event) => {
      if (!sheet.classList.contains("open")) return;
      if (event.target.closest("button, a")) return;
      dragStartY = event.clientY;
      dragPointerId = event.pointerId;
      dragDeltaY = 0;
      sheet.classList.add("dragging");
      sheetDragHandle.setPointerCapture(event.pointerId);
    });
    sheetDragHandle.addEventListener("pointermove", (event) => {
      if (dragStartY === null || dragPointerId !== event.pointerId) return;
      dragDeltaY = Math.max(0, event.clientY - dragStartY);
      sheet.style.setProperty("--sheet-drag-y", `${dragDeltaY}px`);
    });
    function endSheetDrag(event) {
      if (dragStartY === null || dragPointerId !== event.pointerId) return;
      sheet.classList.remove("dragging");
      dragStartY = null;
      dragPointerId = null;
      if (dragDeltaY > 90) closeSheet();
      else sheet.style.setProperty("--sheet-drag-y", "0px");
      dragDeltaY = 0;
    }
    sheetDragHandle.addEventListener("pointerup", endSheetDrag);
    sheetDragHandle.addEventListener("pointercancel", endSheetDrag);
    window.addEventListener("hashchange", () => setLens(location.hash.slice(1), false));
    document.addEventListener("scroll", updateSeen, { passive: true });
    if (location.hash) setLens(location.hash.slice(1), false);
    updateSeen();
  </script>
</body>
</html>
"""
    return (
        template.replace("@@TITLE@@", html.escape(title))
        .replace("@@BODY@@", body)
        .replace("@@SOURCE_JSON@@", source_json)
        .replace("@@INSIGHT_JSON@@", insight_json)
        .replace("@@ACTIVE_PAGE@@", html.escape(active_page))
    )


def render_source_links(
    source_keys: list[str],
    source_map: dict[str, SourceItem],
) -> str:
    """Render source links for a dense row."""
    links: list[str] = []
    for source_key in source_keys:
        source = source_map.get(source_key)
        if not source:
            continue
        links.append(
            '<a class="source-link" href="#" '
            f'data-source-key="{html.escape(source_key)}">'
            f"{html.escape(source.original_title)}</a>"
        )
    return "".join(links)


def render_warning_block(warnings: list[str]) -> str:
    """Render warnings as diagnostics."""
    if not warnings:
        return ""
    items = "".join(f"<li>{html.escape(warning)}</li>" for warning in warnings)
    return f'<details class="diagnostics"><summary>Warnings</summary><ul>{items}</ul></details>'


def source_label(source_key: str, source_map: dict[str, SourceItem]) -> str:
    """Return a readable source label."""
    source = source_map.get(source_key)
    if not source:
        return html.escape(source_key)
    return html.escape(source.original_title)


def count_category_sources(briefing: DenseCategoryBriefing) -> int:
    """Count unique source keys placed in categories."""
    return len(
        {
            source_key
            for category in briefing.categories
            for item in category.items
            for source_key in item.source_keys
        }
    )


def render_html_shell(
    *,
    title: str,
    body: str,
    sources: list[SourceItem],
    active_page: str,
) -> str:
    """Render shared dense prototype shell."""
    source_json = json.dumps(
        {source.source_key: source.to_manifest_dict() for source in sources},
        ensure_ascii=False,
    ).replace("</", "<\\/")
    template = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>@@TITLE@@</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #fbfbfa;
      --ink: #1f1f1d;
      --muted: #6b6a66;
      --line: #d9d8d4;
      --panel: #ffffff;
      --accent: #2f6f4e;
      --soft: #f1f0eb;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #171817;
        --ink: #f1f0ec;
        --muted: #aaa8a0;
        --line: #383a36;
        --panel: #222420;
        --accent: #8cc7a2;
        --soft: #20221f;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 15px/1.45 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
    }
    main {
      width: min(980px, calc(100vw - 28px));
      margin: 0 auto;
      padding: 26px 0 92px;
    }
    header {
      border-bottom: 1px solid var(--line);
      margin-bottom: 18px;
      padding-bottom: 14px;
    }
    h1 {
      font-size: 1.35rem;
      line-height: 1.15;
      letter-spacing: 0;
      margin: 0 0 6px;
    }
    h2 {
      font-size: 1.05rem;
      line-height: 1.2;
      letter-spacing: 0;
      margin: 0 0 6px;
    }
    h3 {
      font-size: .95rem;
      line-height: 1.25;
      letter-spacing: 0;
      margin: 0 0 5px;
    }
    p { margin: 0 0 8px; }
    header p,
    .section-summary,
    .why {
      color: var(--muted);
    }
    a {
      color: var(--accent);
      text-underline-offset: 3px;
    }
    .category-nav {
      display: flex;
      gap: 8px;
      overflow-x: auto;
      padding-top: 8px;
      scrollbar-width: none;
    }
    .category-nav a {
      flex: 0 0 auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--ink);
      padding: 6px 9px;
      text-decoration: none;
    }
    .category-section {
      border-bottom: 1px solid var(--line);
      padding: 16px 0;
    }
    .dense-list {
      display: grid;
      gap: 8px;
    }
    .dense-row {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
    }
    .dense-row p {
      max-width: 70rem;
    }
    .source-row {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }
    .source-link {
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--accent);
      display: inline-block;
      max-width: 100%;
      overflow: hidden;
      padding: 4px 7px;
      text-decoration: none;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .feedback-row {
      display: flex;
      gap: 6px;
      margin: 8px 0;
    }
    .feedback-row button,
    .sheet-close {
      appearance: none;
      background: transparent;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--ink);
      cursor: pointer;
      font: inherit;
      padding: 5px 8px;
    }
    .feedback-row button.active {
      background: var(--soft);
      border-color: var(--accent);
    }
    details {
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-top: 10px;
      padding: 8px 10px;
    }
    details summary { cursor: pointer; }
    .reject-list {
      color: var(--muted);
      margin: 8px 0 0 18px;
      padding: 0;
    }
    body.sheet-open { overflow: hidden; }
    .sheet-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,.30);
      opacity: 0;
      transition: opacity 160ms ease;
      z-index: 20;
    }
    .sheet-backdrop.open { opacity: 1; }
    .bottom-sheet {
      --sheet-drag-y: 0px;
      position: fixed;
      left: 50%;
      bottom: 0;
      width: min(720px, 100vw);
      height: min(76vh, 720px);
      background: var(--panel);
      border: 1px solid var(--line);
      border-bottom: 0;
      border-radius: 10px 10px 0 0;
      transform: translate(-50%, calc(100% + 12px));
      transition: transform 180ms ease;
      z-index: 30;
      display: flex;
      flex-direction: column;
      box-shadow: 0 -8px 24px rgba(0,0,0,.18);
    }
    .bottom-sheet.open {
      transform: translate(-50%, var(--sheet-drag-y));
    }
    .sheet-top {
      flex: 0 0 auto;
      padding: 8px 16px 10px;
      border-bottom: 1px solid var(--line);
      touch-action: none;
      cursor: grab;
    }
    .sheet-grabber {
      width: 38px;
      height: 4px;
      border-radius: 2px;
      background: var(--line);
      margin: 0 auto 8px;
    }
    .sheet-actions {
      display: flex;
      justify-content: flex-end;
    }
    .sheet-content {
      overflow: auto;
      padding: 18px 18px 28px;
      -webkit-overflow-scrolling: touch;
    }
    .sheet-title {
      margin: 0 0 8px;
      font-size: 1.08rem;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }
    .sheet-meta {
      color: var(--muted);
      font-size: .86rem;
      line-height: 1.4;
      margin: 0 0 12px;
    }
    .sheet-content ul {
      margin: 8px 0 14px 20px;
      padding: 0;
    }
    .diagnostics {
      color: var(--muted);
      font-size: .9rem;
    }
  </style>
</head>
<body data-page="@@ACTIVE_PAGE@@">
  <main>@@BODY@@</main>
  <div class="sheet-backdrop" id="sheet-backdrop" hidden></div>
  <aside class="bottom-sheet" id="bottom-sheet" aria-hidden="true" role="dialog" aria-modal="true">
    <div class="sheet-top" id="sheet-drag-handle">
      <div class="sheet-grabber" aria-hidden="true"></div>
      <div class="sheet-actions">
        <button class="sheet-close" id="sheet-close" type="button">Close</button>
      </div>
    </div>
    <div class="sheet-content" id="sheet-content"></div>
  </aside>
  <script>
    const SOURCE_MAP = @@SOURCE_JSON@@;
    const sheet = document.getElementById("bottom-sheet");
    const sheetContent = document.getElementById("sheet-content");
    const sheetBackdrop = document.getElementById("sheet-backdrop");
    const sheetClose = document.getElementById("sheet-close");
    const sheetDragHandle = document.getElementById("sheet-drag-handle");
    let dragStartY = null;
    let dragPointerId = null;
    let dragDeltaY = 0;

    function escapeHtml(value) {
      return String(value || "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\\"": "&quot;",
        "'": "&#39;"
      }[char]));
    }
    function escapeAttr(value) {
      return escapeHtml(value).replace(/`/g, "&#96;");
    }
    function keyPointsList(source) {
      const points = source.key_points || [];
      if (!points.length) return "";
      return `<ul>${points.map((point) => `<li>${escapeHtml(point)}</li>`).join("")}</ul>`;
    }
    function sourceSheetContent(sourceKey) {
      const source = SOURCE_MAP[sourceKey];
      if (!source) return null;
      const external = source.url ? `<p><a href="${escapeAttr(source.url)}" target="_blank" rel="noreferrer">Open original</a></p>` : "";
      const summary = source.summary ? `<p>${escapeHtml(source.summary)}</p>` : "";
      return `<div>
        <h2 class="sheet-title">${escapeHtml(source.original_title)}</h2>
        <div class="sheet-meta">${escapeHtml(source.source_name || source.kind)} · ${escapeHtml(source.published_at || "undated")} · <code>${escapeHtml(sourceKey)}</code></div>
        ${summary}
        ${keyPointsList(source)}
        ${external}
      </div>`;
    }
    function openSheet(contentHtml) {
      if (!contentHtml) return;
      sheetContent.innerHTML = contentHtml;
      sheet.style.setProperty("--sheet-drag-y", "0px");
      sheetBackdrop.hidden = false;
      requestAnimationFrame(() => {
        sheetBackdrop.classList.add("open");
        sheet.classList.add("open");
      });
      sheet.setAttribute("aria-hidden", "false");
      document.body.classList.add("sheet-open");
    }
    function closeSheet() {
      sheetBackdrop.classList.remove("open");
      sheet.classList.remove("open");
      sheet.setAttribute("aria-hidden", "true");
      document.body.classList.remove("sheet-open");
      sheet.style.setProperty("--sheet-drag-y", "0px");
      window.setTimeout(() => {
        if (!sheet.classList.contains("open")) {
          sheetBackdrop.hidden = true;
          sheetContent.innerHTML = "";
        }
      }, 190);
    }
    document.addEventListener("click", (event) => {
      const sourceLink = event.target.closest(".source-link");
      if (sourceLink) {
        event.preventDefault();
        openSheet(sourceSheetContent(sourceLink.dataset.sourceKey));
        return;
      }
      const feedback = event.target.closest("[data-feedback]");
      if (feedback) {
        const row = feedback.closest(".dense-row");
        row.querySelectorAll("[data-feedback]").forEach((button) => button.classList.remove("active"));
        feedback.classList.add("active");
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && sheet.classList.contains("open")) closeSheet();
    });
    sheetBackdrop.addEventListener("click", closeSheet);
    sheetClose.addEventListener("click", closeSheet);
    sheetDragHandle.addEventListener("pointerdown", (event) => {
      if (!sheet.classList.contains("open")) return;
      if (event.target.closest("button, a")) return;
      dragStartY = event.clientY;
      dragPointerId = event.pointerId;
      dragDeltaY = 0;
      sheetDragHandle.setPointerCapture(event.pointerId);
    });
    sheetDragHandle.addEventListener("pointermove", (event) => {
      if (dragStartY === null || dragPointerId !== event.pointerId) return;
      dragDeltaY = Math.max(0, event.clientY - dragStartY);
      sheet.style.setProperty("--sheet-drag-y", `${dragDeltaY}px`);
    });
    function endSheetDrag(event) {
      if (dragStartY === null || dragPointerId !== event.pointerId) return;
      dragStartY = null;
      dragPointerId = null;
      if (dragDeltaY > 90) {
        closeSheet();
      } else {
        sheet.style.setProperty("--sheet-drag-y", "0px");
      }
      dragDeltaY = 0;
    }
    sheetDragHandle.addEventListener("pointerup", endSheetDrag);
    sheetDragHandle.addEventListener("pointercancel", endSheetDrag);
  </script>
</body>
</html>
"""
    return (
        template.replace("@@TITLE@@", html.escape(title))
        .replace("@@BODY@@", body)
        .replace("@@SOURCE_JSON@@", source_json)
        .replace("@@ACTIVE_PAGE@@", html.escape(active_page))
    )


def slugify(value: str) -> str:
    """Return a simple slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "category"


if __name__ == "__main__":
    raise SystemExit(main())
