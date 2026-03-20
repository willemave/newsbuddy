#!/usr/bin/env python3
"""Preview archetype reactions for recent articles, podcasts, and daily digests."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
VENV_BIN_DIR = PROJECT_ROOT / ".venv" / "bin"

if VENV_PYTHON.exists():
    current_executable = Path(sys.executable)
    if VENV_BIN_DIR not in current_executable.parents:
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])

sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.rule import Rule  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.db import get_db  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.models.schema import Content, DailyNewsDigest  # noqa: E402
from app.services.llm_agents import get_basic_agent  # noqa: E402
from app.services.llm_models import resolve_model  # noqa: E402

logger = get_logger(__name__)
console = Console()

DEFAULT_ARTICLE_COUNT = 2
DEFAULT_PODCAST_COUNT = 2
DEFAULT_DIGEST_COUNT = 2
DEFAULT_MODEL = "openai:gpt-5.4"
SUMMARY_ITEM_LIMIT = 5
STRING_CLIP_LENGTH = 280
FALLBACK_TEXT_CLIP_LENGTH = 700

SYSTEM_PROMPT = """
You are an expert analyst producing three reactions to one content item.

For each section, fully inhabit the mindset of the named person and react as that
person would react. Do not mention that you are analyzing, roleplaying, or using
a framework. Write in plain text, not JSON.

Output exactly this structure:

Paul Graham:
<two shorter paragraphs>

Andy Grove:
<two shorter paragraphs>

Charlie Munger:
<two shorter paragraphs>

Each section should be exactly two compact paragraphs, not one long block, with a
blank line between them.

Paul Graham:
Focus on founders, product taste, leverage, market pull, what small teams could
build, and where incumbents are missing something. Start with what specific
users want badly enough to change behavior, not with abstract market narratives.
Ask what the founders are noticing firsthand, what looks trivial but compounds,
what idea seems unfashionable or underestimated, and what scrappy unscalable
action would reveal the truth fastest. Favor bottom-up observations, user pull,
earned insight from direct contact with reality, and the difference between
plausible-sounding ideas and things people actually want.

Andy Grove:
Focus on strategic inflection points, chokepoints, competition, execution risk,
industry structure, and what leaders should monitor. Treat the item as an
operating problem under pressure, not just an interesting story. Ask what 10x
force may be rewriting the rules, what substitute, complementor, bottleneck, or
structural shift changes competitive position, where complacency or denial might
hide, and what leaders should track weekly. Emphasize consequences for
organization design, strategic posture, painful pivots, and whether management
is facing reality fast enough.

Charlie Munger:
Focus on incentives, second-order effects, multidisciplinary mental models,
moats, and what the market or public is likely misunderstanding. Think like a
diagnostician of misjudgment rather than a pundit. Ask who is being rewarded to
believe what, which biases or psychological tendencies are distorting judgment,
what hidden consequences will emerge after the first-order story, and whether
multiple forces are interacting in a lollapalooza effect. Use a latticework of
models across psychology, economics, competition, engineering, and history
instead of staying inside one domain.
""".strip()


@dataclass(frozen=True)
class PreviewItem:
    """Normalized item payload used for prompt construction and rendering."""

    item_kind: str
    item_id: int
    title: str
    subtitle: str
    prompt_payload: dict[str, Any]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Preview archetype reactions for recent content")
    parser.add_argument(
        "--articles",
        type=int,
        default=DEFAULT_ARTICLE_COUNT,
        help=f"Number of recent completed articles to include (default: {DEFAULT_ARTICLE_COUNT}).",
    )
    parser.add_argument(
        "--podcasts",
        type=int,
        default=DEFAULT_PODCAST_COUNT,
        help=f"Number of recent completed podcasts to include (default: {DEFAULT_PODCAST_COUNT}).",
    )
    parser.add_argument(
        "--daily-digests",
        type=int,
        default=DEFAULT_DIGEST_COUNT,
        help=f"Number of recent daily digests to include (default: {DEFAULT_DIGEST_COUNT}).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"Model spec or hint to use (default: {DEFAULT_MODEL}).",
    )
    return parser.parse_args()


def _format_timestamp(value: datetime | date | None) -> str:
    """Render timestamps consistently for console output."""
    if value is None:
        return "unknown"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return value.isoformat()


def _clean_text(value: str, limit: int = STRING_CLIP_LENGTH) -> str:
    """Collapse whitespace and clip long text values."""
    collapsed = " ".join(value.split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: limit - 3].rstrip()}..."


def _compact_json_value(value: Any) -> Any:
    """Reduce summary payloads into small prompt-friendly structures."""
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, list):
        compact_items = [_compact_json_value(item) for item in value[:SUMMARY_ITEM_LIMIT]]
        return compact_items
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, raw_value in value.items():
            if key in {"full_markdown", "content", "transcript"}:
                continue
            compact[key] = _compact_json_value(raw_value)
        return compact
    return value


def _extract_fallback_text(metadata: dict[str, Any]) -> str | None:
    """Return a short fallback snippet when no summary exists."""
    for key in ("description", "excerpt", "dek", "content", "transcript"):
        candidate = metadata.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return _clean_text(candidate, limit=FALLBACK_TEXT_CLIP_LENGTH)
    return None


def _build_content_summary_payload(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a compact summary payload from content metadata."""
    summary = metadata.get("summary")
    if isinstance(summary, dict) and summary:
        return _compact_json_value(summary)
    return None


def _build_content_preview_item(content: Content) -> PreviewItem:
    """Normalize one article or podcast row for prompting."""
    metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
    summary_payload = _build_content_summary_payload(metadata)
    fallback_text = _extract_fallback_text(metadata) if summary_payload is None else None
    source = (
        content.source.strip()
        if isinstance(content.source, str) and content.source.strip()
        else "Unknown"
    )
    prompt_payload = {
        "item_kind": content.content_type,
        "item_id": content.id,
        "title": content.title or f"Untitled {content.content_type}",
        "url": content.url,
        "source": source,
        "created_at": _format_timestamp(content.created_at),
        "summary_payload": summary_payload,
        "fallback_text": fallback_text,
    }
    subtitle = f"{source} | {_format_timestamp(content.created_at)}"
    return PreviewItem(
        item_kind=content.content_type,
        item_id=content.id,
        title=content.title or f"Untitled {content.content_type}",
        subtitle=subtitle,
        prompt_payload=prompt_payload,
    )


def _build_digest_preview_item(digest: DailyNewsDigest) -> PreviewItem:
    """Normalize one daily digest row for prompting."""
    key_points = [
        _clean_text(point)
        for point in digest.key_points
        if isinstance(digest.key_points, list) and isinstance(point, str) and point.strip()
    ]
    prompt_payload = {
        "item_kind": "daily_news_digest",
        "item_id": digest.id,
        "title": digest.title,
        "local_date": digest.local_date.isoformat(),
        "generated_at": _format_timestamp(digest.generated_at),
        "summary": _clean_text(digest.summary, limit=FALLBACK_TEXT_CLIP_LENGTH),
        "key_points": key_points[:SUMMARY_ITEM_LIMIT],
        "source_count": digest.source_count,
    }
    subtitle = (
        f"local_date={digest.local_date.isoformat()} | "
        f"source_count={digest.source_count} | "
        f"generated_at={_format_timestamp(digest.generated_at)}"
    )
    return PreviewItem(
        item_kind="daily_news_digest",
        item_id=digest.id,
        title=digest.title,
        subtitle=subtitle,
        prompt_payload=prompt_payload,
    )


def load_preview_items(
    db: Session,
    *,
    article_count: int,
    podcast_count: int,
    digest_count: int,
) -> list[PreviewItem]:
    """Load the latest items from each requested bucket."""
    items: list[PreviewItem] = []

    if article_count > 0:
        articles = (
            db.query(Content)
            .filter(Content.content_type == "article", Content.status == "completed")
            .order_by(Content.created_at.desc(), Content.id.desc())
            .limit(article_count)
            .all()
        )
        items.extend(_build_content_preview_item(article) for article in articles)

    if podcast_count > 0:
        podcasts = (
            db.query(Content)
            .filter(Content.content_type == "podcast", Content.status == "completed")
            .order_by(Content.created_at.desc(), Content.id.desc())
            .limit(podcast_count)
            .all()
        )
        items.extend(_build_content_preview_item(podcast) for podcast in podcasts)

    if digest_count > 0:
        digests = (
            db.query(DailyNewsDigest)
            .order_by(DailyNewsDigest.local_date.desc(), DailyNewsDigest.id.desc())
            .limit(digest_count)
            .all()
        )
        items.extend(_build_digest_preview_item(digest) for digest in digests)

    return items


def _build_user_prompt(item: PreviewItem) -> str:
    """Build the item-specific user prompt for generation."""
    payload_json = json.dumps(item.prompt_payload, indent=2, sort_keys=True)
    return (
        "React to this content item as Paul Graham, Andy Grove, and Charlie Munger.\n\n"
        f"Item payload:\n{payload_json}\n"
    )


def generate_reaction_set(
    *,
    model_spec: str,
    item: PreviewItem,
) -> str:
    """Generate one reaction block for a preview item."""
    agent = get_basic_agent(model_spec, str, SYSTEM_PROMPT)
    result = agent.run_sync(_build_user_prompt(item))
    output = result.output if hasattr(result, "output") else result.data
    return str(output).strip()


def _render_prompt_context(item: PreviewItem) -> str:
    """Render the compact source payload for terminal display."""
    payload = item.prompt_payload.copy()
    if payload.get("summary_payload") is None:
        payload.pop("summary_payload", None)
    if payload.get("fallback_text") is None:
        payload.pop("fallback_text", None)
    return json.dumps(payload, indent=2, sort_keys=True)


def render_reaction_set(item: PreviewItem, reaction_text: str) -> None:
    """Print one reaction set in a readable terminal format."""
    header = f"{item.item_kind} #{item.item_id}: {item.title}"
    console.print(
        Panel.fit(
            f"[bold]{header}[/bold]\n[dim]{item.subtitle}[/dim]",
            border_style="cyan",
        )
    )
    console.print("[bold]Source Context[/bold]")
    console.print(_render_prompt_context(item))
    console.print()
    console.print("[bold]Archetype Reactions[/bold]")
    console.print(reaction_text)


def main() -> int:
    """Run the archetype preview script."""
    setup_logging()
    args = parse_args()
    _, model_spec = resolve_model("openai", args.model)

    console.print(
        Panel.fit(
            "[bold cyan]Archetype Reaction Preview[/bold cyan]\n"
            f"model={model_spec} | articles={args.articles} | "
            f"podcasts={args.podcasts} | daily_digests={args.daily_digests}",
            border_style="cyan",
        )
    )

    with get_db() as db:
        items = load_preview_items(
            db,
            article_count=args.articles,
            podcast_count=args.podcasts,
            digest_count=args.daily_digests,
        )

    if not items:
        console.print("[red]No matching items found.[/red]")
        return 1

    failures = 0
    for index, item in enumerate(items, start=1):
        console.print()
        console.print(Rule(title=f"Item {index} of {len(items)}", style="green"))
        try:
            reaction_set = generate_reaction_set(model_spec=model_spec, item=item)
            render_reaction_set(item, reaction_set)
        except Exception as error:  # pragma: no cover - manual preview script
            failures += 1
            logger.exception(
                "Failed to generate archetype reactions",
                extra={
                    "component": "preview_archetype_reactions",
                    "operation": "generate_reaction_set",
                    "item_id": item.item_id,
                    "context_data": {"item_kind": item.item_kind, "title": item.title},
                },
            )
            console.print(
                f"[red]Failed for {item.item_kind} #{item.item_id} ({item.title}): {error}[/red]"
            )

    if failures:
        console.print(f"\n[yellow]Completed with {failures} failure(s).[/yellow]")
        return 1

    console.print("\n[green]Completed successfully.[/green]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
