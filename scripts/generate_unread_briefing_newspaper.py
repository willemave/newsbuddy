"""One-off newspaper-layout generation for the unread-briefing prototype.

The LLM owns page composition. Instead of flat prose chunks, each lens is
generated as a typed block document: passage blocks weighted feature/brief,
figure blocks placing artwork inline (left/right column cuts or full width),
and optional pull quotes. Python renders the blocks verbatim.

Lens membership stays frozen: category lenses come from categories.json and
the personalized lens from personalized.json, both produced by the accepted
DeepSeek runs. Only composition/layout is regenerated here.

Usage:
    uv run python scripts/generate_unread_briefing_newspaper.py \
        --model openrouter:deepseek/deepseek-v4-flash --timeout-seconds 300

Then re-render pages:
    uv run python scripts/render_madlib_style_lab.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.vendor_costs import extract_usage_from_result  # noqa: E402
from scripts.generate_unread_briefing_prototype import (  # noqa: E402
    GeneratedBriefingInsight,
    SourceItem,
    build_fallback_repair_chunk,
    default_local_database_url,
    list_unread_long_form_content,
    merge_usage,
    source_from_content,
    source_keys_from_markdown,
)
from scripts.generate_unread_briefing_variants import run_structured_generation  # noqa: E402
from scripts.render_madlib_style_lab import close_unpaired_insights  # noqa: E402

ROOT = Path("outputs/unread_briefing_prototype/user_1_current")
DEFAULT_MODEL_SPEC = "openrouter:deepseek/deepseek-v4-flash"
MAX_FIGURES_PER_LENS = 6
DEEP_MAX_FIGURES = 12
DEEP_WINDOW_SIZE = 5
PODCAST_FETCH_LIMIT = 12
LLM_ATTEMPTS = 2

LAYOUT_SYSTEM_PROMPT = (
    "You are the layout editor and writer for one section of a Newsly newspaper-style "
    "briefing. You own both the prose and the page composition, expressed as typed "
    "layout blocks matching the structured output schema. Do not invent facts, URLs, "
    "or source keys."
)


class LayoutBlock(BaseModel):
    """One ordered layout block.

    Flat (no union) so cheaper models handle the tool schema reliably;
    per-type field requirements are enforced deterministically afterwards.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["passage", "figure", "pullquote"]
    weight: Literal["feature", "brief"] | None = Field(
        None,
        description=(
            "passage only. feature = a substantial story given room to breathe (1-3 "
            "sentences on one or two sources). brief = a dense scan run packing "
            "several fast news items."
        ),
    )
    markdown: str | None = Field(
        None,
        description=(
            "passage only. Continuous prose with markdown links to the given "
            "newsly:// link_url values; link text should resemble the article title. "
            "No headings, bullets, or lists. Mark 0-2 tappable semantic fragments "
            "with {{insight:id}}...{{/insight}}."
        ),
    )
    insights: list[GeneratedBriefingInsight] = Field(
        default_factory=list,
        description="passage only. Entries matching the {{insight:id}} markers.",
    )
    source_key: str | None = None
    caption: str | None = None
    placement: Literal["left", "right", "full"] | None = None
    text: str | None = None


class GeneratedLayoutBriefing(BaseModel):
    """LLM output: one lens composed as an ordered block document."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=5, max_length=220)
    deck: str = Field(..., min_length=20, max_length=600)
    blocks: list[LayoutBlock] = Field(..., min_length=1)


PROSE_RULES = (
    "Prose rules:\n"
    "- One continuous Mad-Lib style narration across all passage blocks: no "
    "headings, bullets, item rows, or per-article sections.\n"
    "- Link every source exactly once somewhere in the passage prose as a "
    "standard markdown link whose text is a natural, title-resembling part of "
    "the sentence. Example: 'OpenAI reports that "
    "[97.9% of employees now use Codex](newsly://briefing/news/17389), up from "
    "40% in August.' Never write bare bracketed URLs like "
    "'[newsly://briefing/news/17389]'.\n"
    "- Use source keys and link_urls exactly as given. Never invent them.\n"
)

NEWS_LAYOUT_RULES = (
    "- Mark 2-4 interesting semantic fragments across the whole section with "
    "{{insight:id}}...{{/insight}} and matching insight entries (unique ids).\n\n"
    "Layout rules — this is the part you own:\n"
    "- This is the fast tier: short and punchy. Use weight=brief passages almost "
    "exclusively — several sources each, telegraphic energy, highest useful "
    "density. At most 1-2 weight=feature passages, only if a story genuinely "
    "towers over the rest.\n"
    "- Interleave the rhythm: a reader should feel momentum, not a wall.\n"
    "- Figures only if sources have has_artwork=true: place each immediately "
    "after the passage that discusses that source, never as the first block. "
    f"At most {MAX_FIGURES_PER_LENS}.\n"
    "- Add at most 1 pullquote block if one line truly earns it.\n"
)

DEEP_LAYOUT_RULES = (
    "\nLayout rules — this is the part you own:\n"
    "- Give each source its own weight=feature passage of 1-3 compact "
    "sentences: what it is, the core claim or numbers, why it matters. "
    "Informational register, not conversational — no rhetorical setups ('the "
    "real twist is', 'walks through'), no reviewer voice, no filler. State "
    "facts and claims directly; the reader is deciding what deserves longer "
    "attention. Do not compress multiple sources into one passage unless two "
    "are genuinely the same story.\n"
    "- In every passage, mark 2-3 semantic fragments with "
    "{{insight:id}}...{{/insight}} and matching insight entries (unique ids "
    "across the section): these are tap-to-learn-more deep dives. Pick the "
    "concrete claims, numbers, and mechanisms worth expanding — not vague "
    "phrases. Keep each learn_more to 1-2 tight sentences that teach "
    "something the passage doesn't say, with at most 2 follow_up_questions.\n"
    "- Figures are required when artwork exists: include a figure block for "
    "most has_artwork=true sources, each immediately after its passage, never "
    f"as the first block. At most {DEEP_MAX_FIGURES}. placement=left/right for "
    "column cuts; placement=full only for the single strongest story.\n"
    "- Write each figure caption yourself: short, grounded, newspaper-style, "
    "not a copy of the title.\n"
    "- Add 1-2 pullquote blocks at natural pause points.\n"
)


def build_layout_prompt(
    *,
    lens_title: str,
    tier: str,
    sources: list[SourceItem],
    images: dict[str, str],
    window_note: str = "",
) -> str:
    payload = {
        "lens": lens_title,
        "source_count": len(sources),
        "sources": [
            {
                **source.to_prompt_dict(),
                "has_artwork": source.source_key in images,
            }
            for source in sources
        ],
    }
    if tier == "news":
        intro = (
            f"Compose the '{lens_title}' news section of an unread-news newspaper "
            "briefing as an ordered list of layout blocks. You own the pacing and "
            "composition.\n\n"
            "Reader: Willem, a technical product-builder scanning fast news. He "
            "wants to clear this category quicker than reading the raw list.\n\n"
        )
        rules = NEWS_LAYOUT_RULES
    else:
        noun = "podcast episodes" if tier == "audio" else "long-form articles"
        intro = (
            f"Compose the '{lens_title}' section of an unread-news newspaper "
            f"briefing as an ordered list of layout blocks covering {noun}. "
            "You own the pacing and composition.\n\n"
            "Reader: Willem, a technical product-builder deciding what deserves "
            "his longer attention. This tier is slower and roomier than the news "
            "tier.\n\n"
        )
        rules = DEEP_LAYOUT_RULES
    return (
        intro
        + window_note
        + PROSE_RULES
        + rules
        + "\nInput JSON:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def deterministic_layout(
    *,
    title: str,
    deck: str,
    sources: list[SourceItem],
) -> GeneratedLayoutBriefing:
    """Fallback: brief passages in source order, no figures."""
    blocks: list[LayoutBlock] = []
    chunk_size = 4
    for start in range(0, len(sources), chunk_size):
        chunk = build_fallback_repair_chunk(
            sources=sources[start : start + chunk_size],
            chunk_index=(start // chunk_size) + 1,
        )
        blocks.append(LayoutBlock(type="passage", weight="brief", markdown=chunk.markdown))
    return GeneratedLayoutBriefing(title=title, deck=deck, blocks=blocks)


def repair_layout(
    layout: GeneratedLayoutBriefing,
    *,
    lens_id: str,
    sources: list[SourceItem],
    images: dict[str, str],
    max_figures: int = MAX_FIGURES_PER_LENS,
) -> tuple[GeneratedLayoutBriefing, list[str]]:
    """Deterministic guardrails over the model's composition."""
    warnings: list[str] = []
    lens_keys = {source.source_key for source in sources}
    source_by_key = {source.source_key: source for source in sources}

    blocks: list[LayoutBlock] = []
    figure_count = 0
    seen_passage = False
    for block in layout.blocks:
        if block.type == "figure":
            if (
                not block.source_key
                or block.source_key not in lens_keys
                or block.source_key not in images
                or not block.placement
            ):
                warnings.append(f"dropped malformed figure ({block.source_key})")
                continue
            if figure_count >= max_figures:
                warnings.append(f"dropped figure over cap: {block.source_key}")
                continue
            if not seen_passage:
                warnings.append(f"dropped leading figure {block.source_key}")
                continue
            figure_count += 1
        if block.type == "pullquote":
            if not block.text or len(block.text.strip()) < 20:
                warnings.append("dropped pullquote without usable text")
                continue
            if block.source_key and block.source_key not in lens_keys:
                block = block.model_copy(update={"source_key": None})
                warnings.append("cleared unknown pullquote source_key")
        if block.type == "passage":
            if (not block.markdown or len(block.markdown.strip()) < 40) and (
                block.text and len(block.text.strip()) >= 40
            ):
                # Models sometimes put passage prose in the pullquote text field.
                block = block.model_copy(update={"markdown": block.text, "text": None})
                warnings.append("recovered passage prose from text field")
            if not block.markdown or len(block.markdown.strip()) < 40:
                warnings.append("dropped passage without usable markdown")
                continue
            repaired_markdown = close_unpaired_insights(block.markdown)
            if repaired_markdown != block.markdown:
                block = block.model_copy(update={"markdown": repaired_markdown})
                warnings.append("closed unpaired insight marker(s)")
            seen_passage = True
        blocks.append(block)

    covered: set[str] = set()
    for block in blocks:
        if block.type == "passage" and block.markdown:
            covered |= source_keys_from_markdown(block.markdown)
    unknown = covered - lens_keys
    if unknown:
        warnings.append(f"passages reference unknown source keys: {sorted(unknown)}")
    missing = sorted(lens_keys - covered)
    if missing:
        warnings.append(f"appended deterministic repair passage for {len(missing)} sources")
        repair_chunk = build_fallback_repair_chunk(
            sources=[source_by_key[key] for key in missing],
            chunk_index=0,
        )
        blocks.append(LayoutBlock(type="passage", weight="brief", markdown=repair_chunk.markdown))

    # Keep insight ids unique across lenses rendered on one page.
    prefixed_blocks: list[LayoutBlock] = []
    for block in blocks:
        if block.type == "passage" and block.insights:
            markdown = block.markdown or ""
            insights = []
            for insight in block.insights:
                new_id = f"{lens_id}_{insight.insight_id}".replace("-", "_")
                markdown = markdown.replace(
                    f"{{{{insight:{insight.insight_id}}}}}",
                    f"{{{{insight:{new_id}}}}}",
                )
                insights.append(insight.model_copy(update={"insight_id": new_id}))
            block = block.model_copy(update={"markdown": markdown, "insights": insights})
        prefixed_blocks.append(block)

    return layout.model_copy(update={"blocks": prefixed_blocks}), warnings


def run_layout_call_with_retry(
    *,
    lens_id: str,
    prompt: str,
    model_spec: str,
    timeout_seconds: int,
    label: str,
) -> tuple[GeneratedLayoutBriefing, dict[str, int | None] | None]:
    last_error: Exception | None = None
    for attempt in range(1, LLM_ATTEMPTS + 1):
        try:
            result = run_structured_generation(
                model_spec=model_spec,
                output_type=GeneratedLayoutBriefing,
                system_prompt=LAYOUT_SYSTEM_PROMPT,
                prompt=prompt,
                timeout_seconds=timeout_seconds,
                label=label,
            )
            return result.output, extract_usage_from_result(result)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(
                f"  [{lens_id}] attempt {attempt}/{LLM_ATTEMPTS} failed: {exc}",
                file=sys.stderr,
                flush=True,
            )
    raise last_error if last_error else RuntimeError("layout call failed")


def prefix_window_insights(layout: GeneratedLayoutBriefing, prefix: str) -> GeneratedLayoutBriefing:
    blocks: list[LayoutBlock] = []
    for block in layout.blocks:
        if block.type == "passage" and block.insights:
            markdown = block.markdown or ""
            insights = []
            for insight in block.insights:
                new_id = f"{prefix}{insight.insight_id}"
                markdown = markdown.replace(
                    f"{{{{insight:{insight.insight_id}}}}}",
                    f"{{{{insight:{new_id}}}}}",
                )
                insights.append(insight.model_copy(update={"insight_id": new_id}))
            block = block.model_copy(update={"markdown": markdown, "insights": insights})
        blocks.append(block)
    return layout.model_copy(update={"blocks": blocks})


def generate_lens_layout(
    *,
    lens_id: str,
    title: str,
    deck: str,
    tier: str,
    sources: list[SourceItem],
    images: dict[str, str],
    model_spec: str,
    timeout_seconds: int,
    use_llm: bool,
) -> tuple[dict[str, Any], dict[str, int | None] | None]:
    usage: dict[str, int | None] | None = None
    warnings: list[str] = []
    windows = (
        [sources[i : i + DEEP_WINDOW_SIZE] for i in range(0, len(sources), DEEP_WINDOW_SIZE)]
        if tier != "news" and len(sources) > DEEP_WINDOW_SIZE
        else [sources]
    )
    if use_llm:
        blocks: list[LayoutBlock] = []
        layout: GeneratedLayoutBriefing | None = None
        for window_index, window_sources in enumerate(windows, start=1):
            print(
                f"Generating layout for lens '{lens_id}' "
                f"(window {window_index}/{len(windows)}, {len(window_sources)} sources)...",
                file=sys.stderr,
                flush=True,
            )
            per_window_figures = max(1, -(-DEEP_MAX_FIGURES // len(windows)))
            window_note = (
                f"This is part {window_index} of {len(windows)} of the section; the "
                "parts are concatenated invisibly, so continue the narration "
                "without re-introducing the section. Include at most "
                f"{per_window_figures} figure blocks in this part.\n\n"
                if len(windows) > 1
                else ""
            )
            try:
                window_layout, window_usage = run_layout_call_with_retry(
                    lens_id=lens_id,
                    prompt=build_layout_prompt(
                        lens_title=title,
                        tier=tier,
                        sources=window_sources,
                        images=images,
                        window_note=window_note,
                    ),
                    model_spec=model_spec,
                    timeout_seconds=timeout_seconds,
                    label=f"layout lens {lens_id} window {window_index}",
                )
                usage = merge_usage(usage, window_usage)
                if len(windows) > 1:
                    window_layout = prefix_window_insights(window_layout, f"w{window_index}_")
            except Exception as exc:  # noqa: BLE001
                warnings.append(
                    f"window {window_index} LLM generation failed ({exc}); "
                    "used deterministic window"
                )
                window_layout = deterministic_layout(title=title, deck=deck, sources=window_sources)
            blocks.extend(window_layout.blocks)
            layout = window_layout
        layout = layout or deterministic_layout(title=title, deck=deck, sources=sources)
        layout = layout.model_copy(update={"blocks": blocks})
    else:
        layout = deterministic_layout(title=title, deck=deck, sources=sources)

    layout = layout.model_copy(update={"title": title, "deck": deck})
    layout, repair_warnings = repair_layout(
        layout,
        lens_id=lens_id,
        sources=sources,
        images=images,
        max_figures=MAX_FIGURES_PER_LENS if tier == "news" else DEEP_MAX_FIGURES,
    )
    warnings.extend(repair_warnings)
    for warning in warnings:
        print(f"  [{lens_id}] {warning}", file=sys.stderr, flush=True)
    return (
        {
            "lens_id": lens_id,
            "title": title,
            "deck": deck,
            "tier": tier,
            "source_count": len(sources),
            "source_keys": [source.source_key for source in sources],
            "layout": layout.model_dump(mode="json"),
            "warnings": warnings,
        },
        usage,
    )


def freeze_podcast_sources(*, user_id: int, refresh: bool) -> list[dict[str, Any]]:
    """Freeze the user's unread podcasts once, mirroring the snapshot approach."""
    path = ROOT / "podcasts.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())["sources"]

    from dataclasses import asdict

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(default_local_database_url("newsly"), pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        rows, total = list_unread_long_form_content(
            db,
            user_id=user_id,
            limit=PODCAST_FETCH_LIMIT,
            content_types=["podcast"],
        )
        sources = [asdict(source_from_content(db, row, body_excerpt_chars=1600)) for row in rows]
    path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "user_id": user_id,
                "total_unread_podcasts": total,
                "fetched": len(sources),
                "sources": sources,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(
        f"froze {len(sources)} of {total} unread podcasts to {path}",
        file=sys.stderr,
        flush=True,
    )
    return sources


def load_lens_definitions(
    *,
    podcast_keys: list[str],
    article_keys: list[str],
) -> list[dict[str, Any]]:
    """Tiered lenses: podcasts and articles get room; news stays fast, by category."""
    personalized = json.loads((ROOT / "personalized.json").read_text())
    categories = json.loads((ROOT / "categories.json").read_text())
    lenses: list[dict[str, Any]] = []
    if podcast_keys:
        lenses.append(
            {
                "lens_id": "podcasts",
                "title": "Podcasts",
                "tier": "audio",
                "deck": "Unread episodes, narrated — select any phrase to dig deeper.",
                "source_keys": podcast_keys,
            }
        )
    if article_keys:
        lenses.append(
            {
                "lens_id": "articles",
                "title": "Articles",
                "tier": "longform",
                "deck": "The long reads, given room — select any phrase to dig deeper.",
                "source_keys": article_keys,
            }
        )
    for_you_keys = [
        key for key in personalized["prose_lens"]["source_keys"] if key.startswith("news:")
    ]
    if for_you_keys:
        lenses.append(
            {
                "lens_id": "for-you",
                "title": "For you",
                "tier": "news",
                "deck": personalized["prose_lens"]["briefing"].get("deck") or "",
                "source_keys": for_you_keys,
            }
        )
    for lens in categories["prose_lenses"]:
        news_keys = [key for key in lens["source_keys"] if key.startswith("news:")]
        if not news_keys:
            continue
        lenses.append(
            {
                "lens_id": lens["lens_id"],
                "title": (lens.get("title") or lens["lens_id"]).removesuffix(" briefing"),
                "tier": "news",
                "deck": lens.get("deck") or "",
                "source_keys": news_keys,
            }
        )
    return lenses


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL_SPEC)
    parser.add_argument("--timeout-seconds", type=int, default=420)
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--refresh-podcasts", action="store_true")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument(
        "--lens",
        action="append",
        help="Limit to specific lens ids (repeatable). Default: all lenses.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    snapshot = json.loads((ROOT / "briefing.json").read_text())
    sources = {item["source_key"]: SourceItem(**item) for item in snapshot["sources"]}
    article_keys = [key for key, item in sources.items() if item.kind == "long_article"]

    podcast_items = freeze_podcast_sources(user_id=args.user_id, refresh=args.refresh_podcasts)
    podcast_keys = []
    for item in podcast_items:
        source = SourceItem(**item)
        sources[source.source_key] = source
        podcast_keys.append(source.source_key)

    images = {
        key: f"images/{item.target_id}.jpg"
        for key, item in sources.items()
        if item.kind.startswith("long_") and (ROOT / "images" / f"{item.target_id}.jpg").exists()
    }
    print(f"{len(sources)} sources, {len(images)} with artwork", file=sys.stderr, flush=True)

    all_lens_definitions = load_lens_definitions(
        podcast_keys=podcast_keys, article_keys=article_keys
    )
    lens_definitions = all_lens_definitions
    if args.lens:
        wanted = set(args.lens)
        lens_definitions = [lens for lens in lens_definitions if lens["lens_id"] in wanted]
        if not lens_definitions:
            print(f"No lenses matched {sorted(wanted)}", file=sys.stderr)
            return 1

    usage: dict[str, int | None] | None = None
    lenses_out: list[dict[str, Any]] = []
    for lens in lens_definitions:
        lens_sources = [sources[key] for key in lens["source_keys"] if key in sources]
        lens_payload, lens_usage = generate_lens_layout(
            lens_id=lens["lens_id"],
            title=lens["title"],
            deck=lens["deck"],
            tier=lens["tier"],
            sources=lens_sources,
            images=images,
            model_spec=args.model,
            timeout_seconds=args.timeout_seconds,
            use_llm=not args.skip_llm,
        )
        lenses_out.append(lens_payload)
        usage = merge_usage(usage, lens_usage)

    existing: dict[str, Any] = {}
    output_path = ROOT / "newspaper.json"
    if args.lens and output_path.exists():
        # Partial regeneration: merge into the existing document.
        existing = json.loads(output_path.read_text())
        merged = {lens["lens_id"]: lens for lens in existing.get("lenses", [])}
        for lens in lenses_out:
            merged[lens["lens_id"]] = lens
        order = [lens["lens_id"] for lens in all_lens_definitions]
        lenses_out = [merged[lens_id] for lens_id in order if lens_id in merged]

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": args.model if not args.skip_llm else "deterministic",
        "usage": usage or existing.get("usage"),
        "lenses": lenses_out,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"wrote {output_path}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
