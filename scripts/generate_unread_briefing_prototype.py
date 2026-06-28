"""Generate a one-off source-linked unread briefing prototype.

This script is intentionally experimental. It can refresh a production snapshot
into the local ``newsly_prod`` database, freeze one user's unread news and
long-form rows, ask an LLM for one cohesive scrolling briefing, and write JSON,
Markdown, and HTML artifacts for product review.
"""

from __future__ import annotations

# ruff: noqa: E501
import argparse
import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, create_engine, exists, func, select
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.model_defaults import OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC
from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content, ContentReadStatus, ContentStatusEntry, NewsItem
from app.repositories.content_feed_query import content_sort_timestamp_expr
from app.services.content_bodies import ContentBodyVariant, get_content_body_resolver
from app.services.llm_agents import get_basic_agent
from app.services.llm_models import resolve_model_provider
from app.services.news_article_bodies import get_news_item_article_body_resolver
from app.services.news_feed import list_unread_visible_news_items
from app.services.vendor_costs import (
    estimate_vendor_cost_usd,
    extract_usage_from_result,
)
from app.utils.summary_utils import extract_summary_text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGET_DB = "newsly_prod"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "unread_briefing_prototype"
SNAPSHOT_WARNING = (
    "This prototype freezes the fetched unread set. It does not mark local rows read or "
    "write a product table."
)
INSIGHT_START_RE = re.compile(r"\{\{insight:([A-Za-z0-9_-]+)\}\}")
INSIGHT_END_MARKER = "{{/insight}}"


class GeneratedBriefingSourceRef(BaseModel):
    """A source the prose explicitly refers to."""

    source_key: str = Field(..., description="Input source key, e.g. news:123 or content:456")
    generated_title: str = Field(
        ...,
        description=(
            "Readable link label used by the prose. Keep it close to the source title, "
            "with only light cleanup for readability."
        ),
    )
    role: str = Field(
        ..., description="Why this source appears here, e.g. latest, context, evidence"
    )
    key_points_used: list[str] = Field(
        default_factory=list,
        description=(
            "At most one or two terse source points used in this chunk. Leave empty for "
            "short items when the linked prose already carries the point."
        ),
    )


class GeneratedBriefingInsight(BaseModel):
    """A tappable semantic passage inside generated prose."""

    insight_id: str = Field(
        ...,
        min_length=3,
        max_length=48,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
        description=(
            "Unique id used in inline markdown markers like "
            "{{insight:agent_cloud_shift}}...{{/insight}}."
        ),
    )
    title: str = Field(
        ...,
        min_length=4,
        max_length=90,
        description="Short label for the highlighted passage.",
    )
    learn_more: str = Field(
        ...,
        min_length=40,
        max_length=900,
        description="What a user should learn by digging into this passage.",
    )
    source_keys: list[str] = Field(
        default_factory=list,
        description="Source keys that support or contextualize this passage.",
    )
    follow_up_questions: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="Optional concise questions a reader might ask next.",
    )


class GeneratedBriefingChunk(BaseModel):
    """Stable render chunk for scrolling presentation."""

    chunk_index: int = Field(..., ge=0)
    markdown: str = Field(
        ...,
        min_length=120,
        description=(
            "Continuous briefing prose with markdown links to newsly:// source URLs. "
            "Do not include headings, bullets, or numbered sections."
        ),
    )
    source_refs: list[GeneratedBriefingSourceRef] = Field(default_factory=list)
    insights: list[GeneratedBriefingInsight] = Field(
        default_factory=list,
        description=(
            "Tappable semantic passages marked inline in markdown with "
            "{{insight:id}}...{{/insight}}."
        ),
    )


class GeneratedBriefingOmission(BaseModel):
    """A source intentionally omitted by the model."""

    source_key: str
    reason: str


class GeneratedUnreadBriefing(BaseModel):
    """LLM output for a single unread-set briefing."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=5, max_length=220)
    deck: str = Field(..., min_length=20, max_length=600)
    through_line: str = Field(..., min_length=20, max_length=900)
    chunks: list[GeneratedBriefingChunk] = Field(..., min_length=1)
    omitted_sources: list[GeneratedBriefingOmission] = Field(default_factory=list)


class BriefingSourceSelection(BaseModel):
    """One source selected for the final briefing."""

    source_key: str
    score: int = Field(..., ge=0, le=100)
    reason: str = Field(..., min_length=8, max_length=260)


class BriefingSourceSelectionResult(BaseModel):
    """LLM judgment about which unread sources should make the briefing."""

    model_config = ConfigDict(extra="forbid")

    selection_summary: str = Field(..., min_length=20, max_length=900)
    selected_sources: list[BriefingSourceSelection] = Field(..., min_length=1)
    omitted_sources: list[GeneratedBriefingOmission] = Field(default_factory=list)


@dataclass(frozen=True)
class SourceItem:
    """Frozen source packet handed to the LLM and HTML preview."""

    source_key: str
    kind: str
    target_id: int
    original_title: str
    source_name: str | None
    url: str | None
    published_at: str | None
    sort_timestamp: str | None
    summary: str | None
    key_points: list[str]
    body_excerpt: str | None
    link_url: str

    def to_prompt_dict(self) -> dict[str, Any]:
        """Return a bounded source packet for the LLM prompt."""
        return {
            "source_key": self.source_key,
            "kind": self.kind,
            "link_url": self.link_url,
            "original_title": self.original_title,
            "source_name": self.source_name,
            "published_at": self.published_at,
            "summary": self.summary,
            "key_points": self.key_points,
            "body_excerpt": self.body_excerpt,
        }

    def to_selection_prompt_dict(self) -> dict[str, Any]:
        """Return a compact source packet for the usefulness filter."""
        return {
            "source_key": self.source_key,
            "kind": self.kind,
            "original_title": self.original_title,
            "source_name": self.source_name,
            "published_at": self.published_at,
            "summary": self.summary,
            "key_points": self.key_points[:6],
        }

    def to_manifest_dict(self) -> dict[str, Any]:
        """Return the full source manifest used by JSON/HTML artifacts."""
        return {
            "source_key": self.source_key,
            "kind": self.kind,
            "target_id": self.target_id,
            "original_title": self.original_title,
            "source_name": self.source_name,
            "url": self.url,
            "published_at": self.published_at,
            "sort_timestamp": self.sort_timestamp,
            "summary": self.summary,
            "key_points": self.key_points,
            "body_excerpt": self.body_excerpt,
            "link_url": self.link_url,
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Generate a one-off unread briefing prototype for a local/prod snapshot DB."
    )
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument(
        "--database-url",
        default=None,
        help=(
            "SQLAlchemy database URL to read. Defaults to local newsly_prod unless "
            "DATABASE_URL is explicitly set."
        ),
    )
    parser.add_argument("--target-db", default=DEFAULT_TARGET_DB)
    parser.add_argument(
        "--refresh-production-data",
        action="store_true",
        help="Run scripts/pull_production_db.sh and load the dump into --target-db first.",
    )
    parser.add_argument(
        "--force-load-production",
        action="store_true",
        help="Allow dropping/recreating --target-db when loading a production dump.",
    )
    parser.add_argument("--news-limit", type=int, default=35)
    parser.add_argument("--long-limit", type=int, default=8)
    parser.add_argument(
        "--long-content-type",
        action="append",
        default=None,
        choices=[ContentType.ARTICLE.value, ContentType.PODCAST.value],
        help="Long-form content type to include. Repeatable. Defaults to article only.",
    )
    parser.add_argument("--body-excerpt-chars", type=int, default=1600)
    parser.add_argument("--max-prompt-chars", type=int, default=80_000)
    parser.add_argument(
        "--selection-limit",
        type=int,
        default=80,
        help=(
            "Ask the LLM to select at most this many useful/high-density sources before "
            "writing. Use 0 to include every fetched source."
        ),
    )
    parser.add_argument(
        "--window-size",
        type=int,
        default=0,
        help=(
            "Generate invisible stable prose windows of this many sources and join them "
            "as one briefing. Defaults to 0 for one-shot generation."
        ),
    )
    parser.add_argument(
        "--llm-timeout-seconds",
        type=int,
        default=180,
        help="Per-LLM-call timeout. Use 0 to disable.",
    )
    parser.add_argument(
        "--skip-repair-llm",
        action="store_true",
        help=(
            "Use deterministic paragraphs for missed-link repair chunks instead of "
            "calling the LLM again."
        ),
    )
    parser.add_argument(
        "--archive-run",
        action="store_true",
        help="Write to a timestamped run folder instead of overwriting user_<id>_current.",
    )
    parser.add_argument("--model", default=OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Write source snapshot and prompt only; do not call the model.",
    )
    parser.add_argument(
        "--open-output",
        action="store_true",
        help="Open the generated HTML preview in the default browser.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the prototype generation script."""
    args = parse_args(argv)
    if args.news_limit < 0 or args.long_limit < 0:
        raise SystemExit("news-limit and long-limit must be non-negative")
    if args.news_limit == 0 and args.long_limit == 0:
        raise SystemExit("At least one of news-limit or long-limit must be positive")

    if args.refresh_production_data:
        refresh_production_data(
            target_db=args.target_db,
            force_load=args.force_load_production,
        )

    database_url = args.database_url or default_local_database_url(args.target_db)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        SessionLocal = sessionmaker(bind=engine)
        with SessionLocal() as db:
            db.execute(select(1))
            sources, source_meta = fetch_unread_sources(
                db,
                user_id=args.user_id,
                news_limit=args.news_limit,
                long_limit=args.long_limit,
                long_content_types=args.long_content_type or [ContentType.ARTICLE.value],
                body_excerpt_chars=max(0, args.body_excerpt_chars),
            )
    finally:
        engine.dispose()

    if not sources:
        raise SystemExit(f"No unread sources found for user_id={args.user_id}")

    if args.selection_limit < 0:
        raise SystemExit("selection-limit must be non-negative")

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir_name = (
        f"user_{args.user_id}_{timestamp}" if args.archive_run else f"user_{args.user_id}_current"
    )
    run_dir = args.output_dir / run_dir_name
    if run_dir.exists() and not args.archive_run:
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    warnings = [SNAPSHOT_WARNING]
    initial_source_count = len(sources)
    selection_result: BriefingSourceSelectionResult | None = None
    usage: dict[str, int | None] | None = None
    if args.selection_limit and len(sources) > args.selection_limit:
        if args.skip_llm:
            selection_result = build_deterministic_source_selection(
                sources=sources,
                limit=args.selection_limit,
            )
            sources, selection_warnings = apply_source_selection(
                sources=sources,
                selection=selection_result,
                limit=args.selection_limit,
            )
            warnings.append("LLM source selection skipped; used deterministic recency selection.")
            warnings.extend(selection_warnings)
        else:
            selection_prompt = build_source_selection_prompt(
                user_id=args.user_id,
                sources=sources,
                limit=args.selection_limit,
            )
            selection_prompt_path = run_dir / "selection_prompt.md"
            selection_prompt_path.write_text(selection_prompt, encoding="utf-8")
            try:
                selection_run = run_source_selection(
                    model_spec=args.model,
                    prompt=selection_prompt,
                    timeout_seconds=args.llm_timeout_seconds,
                )
                selection_result = selection_run.output
                usage = merge_usage(usage, extract_usage_from_result(selection_run))
            except Exception as exc:  # noqa: BLE001
                selection_result = build_deterministic_source_selection(
                    sources=sources,
                    limit=args.selection_limit,
                )
                warnings.append(
                    f"LLM source selection failed ({exc}); used deterministic recency selection."
                )
            sources, selection_warnings = apply_source_selection(
                sources=sources,
                selection=selection_result,
                limit=args.selection_limit,
            )
            warnings.extend(selection_warnings)
        if selection_result is not None:
            (run_dir / "selection.json").write_text(
                json.dumps(
                    selection_result.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    sources, budget_warning = apply_prompt_budget(
        sources,
        max_prompt_chars=args.max_prompt_chars,
    )
    if budget_warning:
        warnings.append(budget_warning)

    source_meta["initial_source_count"] = initial_source_count
    source_meta["selected_source_count"] = len(sources)
    source_meta["selection_limit"] = args.selection_limit
    prompt = build_generation_prompt(user_id=args.user_id, sources=sources)

    prompt_path = run_dir / "prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")

    briefing: GeneratedUnreadBriefing | None = None
    estimated_cost_usd: float | None = None
    if not args.skip_llm:
        if args.window_size > 0:
            briefing, generation_usage = run_windowed_generation(
                model_spec=args.model,
                user_id=args.user_id,
                sources=sources,
                window_size=args.window_size,
                timeout_seconds=args.llm_timeout_seconds,
                skip_repair_llm=args.skip_repair_llm,
            )
            usage = merge_usage(usage, generation_usage)
        else:
            result = run_generation(
                model_spec=args.model,
                prompt=prompt,
                timeout_seconds=args.llm_timeout_seconds,
            )
            briefing = result.output
            usage = merge_usage(usage, extract_usage_from_result(result))
        if usage:
            estimated_cost_usd = estimate_vendor_cost_usd(
                provider=resolve_model_provider(args.model),
                model=args.model,
                usage=usage,
                metadata={"source": "unread_briefing_prototype"},
            )
        briefing, normalization_warnings = normalize_generated_briefing(briefing)
        warnings.extend(normalization_warnings)
        warnings.extend(validate_generated_sources(briefing, sources))

    payload = build_output_payload(
        args=args,
        database_url=database_url,
        source_meta=source_meta,
        sources=sources,
        selection_result=selection_result,
        prompt_path=prompt_path,
        briefing=briefing,
        usage=usage,
        estimated_cost_usd=estimated_cost_usd,
        warnings=warnings,
    )
    json_path = run_dir / "briefing.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), "utf-8")

    markdown_path: Path | None = None
    html_path: Path | None = None
    if briefing is not None:
        markdown_path = run_dir / "briefing.md"
        markdown_path.write_text(render_markdown(briefing, sources), encoding="utf-8")
        html_path = run_dir / "briefing.html"
        html_path.write_text(render_html(briefing, sources, payload), encoding="utf-8")

    print(f"Wrote source snapshot: {json_path}")
    print(f"Wrote prompt: {prompt_path}")
    if markdown_path:
        print(f"Wrote markdown: {markdown_path}")
    if html_path:
        print(f"Wrote HTML preview: {html_path}")
        if args.open_output:
            subprocess.run(["open", str(html_path)], check=False)
    if usage:
        print(f"LLM usage: {usage}")
    if estimated_cost_usd is not None:
        print(f"Estimated cost: ${estimated_cost_usd:.6f}")
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    return 0


def refresh_production_data(*, target_db: str, force_load: bool) -> None:
    """Pull a full production dump and restore it into a local target DB."""
    dump_dir = PROJECT_ROOT / ".local_dumps"
    dump_dir.mkdir(parents=True, exist_ok=True)
    dump_path = dump_dir / f"newsly_prod_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.dump"

    subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "pull_production_db.sh"), str(dump_path)],
        cwd=PROJECT_ROOT,
        check=True,
    )

    load_cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "load_production_snapshot.py"),
        "--dump-path",
        str(dump_path),
        "--target-db",
        target_db,
    ]
    if force_load:
        load_cmd.append("--force")
    subprocess.run(load_cmd, cwd=PROJECT_ROOT, check=True)


def default_local_database_url(target_db: str) -> str:
    """Return a local Postgres URL for the snapshot DB using local dev credentials."""
    raw = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://newsly:root@127.0.0.1:5432/newsly",
    )
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)
    parsed = urlparse(raw)
    target = parsed._replace(path=f"/{target_db}")
    return urlunparse(target)


def fetch_unread_sources(
    db: Session,
    *,
    user_id: int,
    news_limit: int,
    long_limit: int,
    long_content_types: list[str],
    body_excerpt_chars: int,
) -> tuple[list[SourceItem], dict[str, Any]]:
    """Fetch and freeze unread news and long-form source packets."""
    news_items: list[NewsItem] = []
    total_unread_news = 0
    if news_limit > 0:
        news_items, total_unread_news = list_unread_visible_news_items(
            db,
            user_id=user_id,
            limit=news_limit,
        )

    long_rows: list[Content] = []
    total_unread_long = 0
    if long_limit > 0:
        long_rows, total_unread_long = list_unread_long_form_content(
            db,
            user_id=user_id,
            limit=long_limit,
            content_types=long_content_types,
        )

    sources: list[SourceItem] = []
    for item in news_items:
        sources.append(source_from_news_item(db, item, body_excerpt_chars=body_excerpt_chars))
    for content in long_rows:
        sources.append(source_from_content(db, content, body_excerpt_chars=body_excerpt_chars))

    sources.sort(key=lambda source: source.sort_timestamp or "", reverse=True)
    return sources, {
        "total_unread_news": total_unread_news,
        "fetched_news": len(news_items),
        "total_unread_long": total_unread_long,
        "fetched_long": len(long_rows),
        "long_content_types": long_content_types,
    }


def list_unread_long_form_content(
    db: Session,
    *,
    user_id: int,
    limit: int,
    content_types: list[str],
) -> tuple[list[Content], int]:
    """Return unread long-form content rows visible to the user."""
    normalized_limit = max(1, min(limit, 100))
    sort_expr = content_sort_timestamp_expr()
    read_exists = exists(
        select(ContentReadStatus.id).where(
            ContentReadStatus.user_id == user_id,
            ContentReadStatus.content_id == Content.id,
        )
    )
    query = (
        db.query(Content)
        .join(
            ContentStatusEntry,
            and_(
                ContentStatusEntry.content_id == Content.id,
                ContentStatusEntry.user_id == user_id,
                ContentStatusEntry.status == "inbox",
            ),
        )
        .filter(Content.status == ContentStatus.COMPLETED.value)
        .filter((Content.classification != "skip") | (Content.classification.is_(None)))
        .filter(Content.content_type.in_(content_types))
        .filter(~read_exists)
    )
    total = int(query.with_entities(func.count(Content.id)).scalar() or 0)
    rows = query.order_by(sort_expr.desc(), Content.id.desc()).limit(normalized_limit).all()
    return rows, total


def source_from_news_item(
    db: Session,
    item: Any,
    *,
    body_excerpt_chars: int,
) -> SourceItem:
    """Build a source packet for a short-form news item."""
    raw_metadata = item.raw_metadata if isinstance(item.raw_metadata, dict) else {}
    raw_summary = raw_metadata.get("summary") if isinstance(raw_metadata, dict) else None
    summary = raw_summary if isinstance(raw_summary, dict) else {}
    key_points = clean_text_list(item.summary_key_points)
    if not key_points:
        key_points = clean_text_list(summary.get("key_points"))
    summary_text = clean_string(item.summary_text) or clean_string(summary.get("summary"))
    title = (
        clean_string(item.summary_title)
        or clean_string(item.article_title)
        or clean_string(summary.get("title"))
        or clean_string(item.article_url)
        or f"News item {item.id}"
    )
    body_excerpt = None
    if body_excerpt_chars > 0:
        try:
            resolved_body = get_news_item_article_body_resolver().resolve(db, news_item=item)
            if resolved_body and resolved_body.text:
                body_excerpt = truncate_text(resolved_body.text, body_excerpt_chars)
        except Exception as exc:  # noqa: BLE001
            body_excerpt = f"[article body unavailable: {exc}]"

    target_id = int(item.id)
    sort_timestamp = serialize_datetime(
        item.published_at or item.processed_at or item.ingested_at or item.created_at
    )
    return SourceItem(
        source_key=f"news:{target_id}",
        kind="news_item",
        target_id=target_id,
        original_title=title,
        source_name=clean_string(item.source_label) or clean_string(item.platform),
        url=clean_string(item.article_url)
        or clean_string(item.canonical_story_url)
        or clean_string(item.canonical_item_url),
        published_at=serialize_datetime(item.published_at),
        sort_timestamp=sort_timestamp,
        summary=summary_text,
        key_points=key_points[:5],
        body_excerpt=body_excerpt,
        link_url=f"newsly://briefing/news/{target_id}",
    )


def source_from_content(
    db: Session,
    content: Content,
    *,
    body_excerpt_chars: int,
) -> SourceItem:
    """Build a source packet for a long-form content row."""
    metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
    raw_summary = metadata.get("summary")
    summary = cast(dict[str, Any], raw_summary) if isinstance(raw_summary, dict) else {}
    summary_text = extract_summary_text(summary)
    key_points = extract_longform_key_points(summary)
    body_excerpt = None
    if body_excerpt_chars > 0:
        try:
            body = get_content_body_resolver().resolve_text(
                db,
                content=content,
                variant=ContentBodyVariant.SOURCE,
            )
            if body:
                body_excerpt = truncate_text(body, body_excerpt_chars)
        except Exception as exc:  # noqa: BLE001
            body_excerpt = f"[content body unavailable: {exc}]"

    if content.id is None:
        raise ValueError("Content row is missing id")
    target_id = int(content.id)
    sort_timestamp = serialize_datetime(
        content.publication_date or content.processed_at or content.created_at
    )
    return SourceItem(
        source_key=f"content:{target_id}",
        kind=f"long_{content.content_type}",
        target_id=target_id,
        original_title=clean_string(content.title) or f"Content {target_id}",
        source_name=clean_string(content.source) or clean_string(content.platform),
        url=clean_string(content.source_url) or clean_string(content.url),
        published_at=serialize_datetime(content.publication_date),
        sort_timestamp=sort_timestamp,
        summary=summary_text,
        key_points=key_points[:8],
        body_excerpt=body_excerpt,
        link_url=f"newsly://briefing/content/{target_id}",
    )


def clean_string(value: Any) -> str | None:
    """Return a stripped non-empty string."""
    if not isinstance(value, str):
        return None
    stripped = " ".join(value.split())
    return stripped or None


def clean_text_list(value: Any) -> list[str]:
    """Normalize a list of free-text points."""
    if not isinstance(value, list):
        return []
    results: list[str] = []
    for item in value:
        text = None
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = item.get("point") or item.get("text") or item.get("content")
        cleaned = clean_string(text)
        if cleaned:
            results.append(cleaned)
    return results


def extract_longform_key_points(summary: dict[str, Any]) -> list[str]:
    """Extract a few robust long-form key points from known summary shapes."""
    if not isinstance(summary, dict):
        return []

    artifact = summary.get("artifact")
    if isinstance(artifact, dict):
        payload = artifact.get("payload")
        if isinstance(payload, dict):
            artifact_points = payload.get("key_points")
            points: list[str] = []
            if isinstance(artifact_points, list):
                for item in artifact_points:
                    if not isinstance(item, dict):
                        continue
                    heading = clean_string(item.get("heading"))
                    content = clean_string(item.get("content"))
                    if heading and content:
                        points.append(f"{heading}: {content}")
                    elif content:
                        points.append(content)
            if points:
                return points

    for key in ("key_points", "points", "bullet_points", "topics", "questions"):
        points = clean_text_list(summary.get(key))
        if points:
            return points

    return []


def serialize_datetime(value: Any) -> str | None:
    """Serialize a datetime-like value."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC).isoformat()
        return value.astimezone(UTC).isoformat()
    if value is None:
        return None
    return str(value)


def truncate_text(text: str, max_chars: int) -> str:
    """Trim text at a readable boundary."""
    compact = "\n".join(line.rstrip() for line in text.strip().splitlines() if line.strip())
    if len(compact) <= max_chars:
        return compact
    trimmed = compact[:max_chars].rstrip()
    boundary = max(trimmed.rfind("\n"), trimmed.rfind(". "), trimmed.rfind(" "))
    if boundary >= max_chars // 2:
        trimmed = trimmed[: boundary + 1].rstrip()
    return f"{trimmed}\n[truncated]"


def apply_prompt_budget(
    sources: list[SourceItem],
    *,
    max_prompt_chars: int,
) -> tuple[list[SourceItem], str | None]:
    """Keep prompt payload under a rough character budget."""
    if max_prompt_chars <= 0:
        return sources, None

    selected: list[SourceItem] = []
    running_chars = 0
    for source in sources:
        size = len(json.dumps(source.to_prompt_dict(), ensure_ascii=False, default=str))
        if selected and running_chars + size > max_prompt_chars:
            break
        selected.append(source)
        running_chars += size

    if len(selected) == len(sources):
        return selected, None
    return (
        selected,
        f"Prompt budget kept {len(selected)} of {len(sources)} fetched unread sources. "
        "Raise --max-prompt-chars or lower body excerpts to include more.",
    )


def build_source_selection_prompt(*, user_id: int, sources: list[SourceItem], limit: int) -> str:
    """Build the LLM prompt for selecting a smaller high-density unread set."""
    target_min = min(len(sources), max(1, round(limit * 0.7)))
    payload = {
        "user_id": user_id,
        "source_count": len(sources),
        "selection_limit": limit,
        "target_minimum": target_min,
        "sources": [source.to_selection_prompt_dict() for source in sources],
    }
    return (
        "Select the unread sources that should make a single Newsly Mad Lib briefing.\n\n"
        "Goal:\n"
        f"- Choose roughly {target_min}-{limit} sources from the full unread set when "
        "there are enough distinct items.\n"
        "- The briefing should feel usefully filtered, not severely pruned. Err toward "
        "including a source when it adds a new angle, weak signal, interesting curiosity, "
        "product intuition, cultural context, or useful texture.\n"
        "- Use judgment, not a strict facts-only rubric. Concrete facts help, but usefulness "
        "can also come from framing, examples, odd details, early signals, or context that "
        "makes the rest of the briefing easier to understand.\n"
        "- Drop exact duplicates and near-duplicates first. Drop genuinely empty or "
        "low-context items next. Do not drop unique items only because they are softer, "
        "smaller, or harder to connect to the dominant arc.\n"
        "- Preserve breadth across themes. Do not collapse the unread set into one central "
        "story if the source set contains several readable threads.\n"
        "- Long reads can be selected for depth even when they are older than short news. "
        "Smaller short news can be selected when it gives a useful example or signal.\n\n"
        "Output rules:\n"
        "- Use exact source_key values from the input. Do not invent or alter keys.\n"
        "- Sort selected_sources in the order they should appear in the briefing, generally "
        "newest-to-oldest with small local regrouping for coherence.\n"
        "- Keep reasons terse and specific, including softer reasons like example, texture, "
        "counterpoint, weak signal, or context.\n"
        "- Put skipped duplicate clusters or clearly low-context categories in omitted_sources "
        "when useful.\n\n"
        "Input JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def run_source_selection(*, model_spec: str, prompt: str, timeout_seconds: int) -> Any:
    """Run the LLM usefulness filter with strict structured output."""
    system_prompt = (
        "You are an editorial ranking system for Newsly. Select a compact, useful source "
        "set for a dense source-linked briefing. Use only exact input source keys."
    )
    agent = get_basic_agent(model_spec, BriefingSourceSelectionResult, system_prompt)
    with llm_call_timeout(timeout_seconds, "source selection"):
        return agent.run_sync(prompt)


def apply_source_selection(
    *,
    sources: list[SourceItem],
    selection: BriefingSourceSelectionResult,
    limit: int,
) -> tuple[list[SourceItem], list[str]]:
    """Apply an LLM source selection while preserving only valid unique keys."""
    source_by_key = {source.source_key: source for source in sources}
    selected: list[SourceItem] = []
    seen: set[str] = set()
    invalid_keys: list[str] = []
    duplicate_keys: list[str] = []

    for item in selection.selected_sources:
        key = item.source_key
        if key in seen:
            duplicate_keys.append(key)
            continue
        if key not in source_by_key:
            invalid_keys.append(key)
            continue
        selected.append(source_by_key[key])
        seen.add(key)
        if len(selected) >= limit:
            break

    warnings: list[str] = []
    if invalid_keys:
        warnings.append(f"Source selection ignored unknown keys: {sorted(set(invalid_keys))}")
    if duplicate_keys:
        warnings.append(f"Source selection ignored duplicate keys: {sorted(set(duplicate_keys))}")
    if not selected:
        selected = sources[:limit]
        warnings.append(
            "Source selection returned no valid keys; used deterministic recency fallback."
        )
    if len(selection.selected_sources) > limit:
        warnings.append(
            f"Source selection returned {len(selection.selected_sources)} keys; kept first {limit}."
        )
    return selected, warnings


def build_deterministic_source_selection(
    *,
    sources: list[SourceItem],
    limit: int,
) -> BriefingSourceSelectionResult:
    """Build a simple recency-based fallback selection."""
    selected = sources[:limit]
    omitted = sources[limit:]
    return BriefingSourceSelectionResult(
        selection_summary=(
            f"Selected the {len(selected)} most recent unread sources because the LLM "
            "selection pass was unavailable."
        ),
        selected_sources=[
            BriefingSourceSelection(
                source_key=source.source_key,
                score=max(0, 100 - index),
                reason="Recency fallback selected this source for prototype continuity.",
            )
            for index, source in enumerate(selected)
        ],
        omitted_sources=[
            GeneratedBriefingOmission(
                source_key=source.source_key,
                reason="Omitted by deterministic recency fallback after the selection limit.",
            )
            for source in omitted[: min(50, len(omitted))]
        ],
    )


def build_generation_prompt(*, user_id: int, sources: list[SourceItem]) -> str:
    """Build the model prompt for one stable unread-set briefing."""
    payload = {
        "user_id": user_id,
        "generation_contract": {
            "stable_chunks": True,
            "mark_read_policy": "Only mark a source read after the user scrolls past its link.",
            "source_link_rule": (
                "Every source mention must link with the exact link_url from its source packet."
            ),
        },
        "source_count": len(sources),
        "sources": [source.to_prompt_dict() for source in sources],
    }
    return (
        "Write one cohesive Newsly unread briefing for the frozen source set below.\n\n"
        "Product feel:\n"
        "- This is one long scrolling Mad Lib style briefing, not a list of cards and not "
        "a set of titled sections.\n"
        "- Make the output information-dense, but not facts-only. Mix concrete details with "
        "useful framing, examples, weak signals, context, and short connective observations.\n"
        "- Use connective tissue between source links so the briefing has a loose arc from "
        "newest material into context, but do not force perfect transitions.\n"
        "- Stay grounded in the provided source packets. You may write light connective "
        "tissue and synthesis, but do not invent unsupported events, numbers, quotes, or "
        "causal claims.\n"
        "- Article/source links should visually resemble their original titles. You may "
        "lightly shorten, normalize, or clarify a title, but do not replace it with a "
        "generic phrase like 'this story' or 'the report.'\n"
        "- Integrate title-like source links as sentence material, not citation footnotes. "
        "Prefer forms like '[Source title](link) reported...', '...as [Source title](link) "
        "argues', or '...alongside [Source title](link).' Avoid ending a complete sentence "
        "with a parenthetical source-link citation.\n"
        "- It is acceptable if a sentence bends a little around a title-like link label; "
        "the title-like source link matters more than perfect prose.\n"
        "- Each source reference must be a markdown link using the exact source link_url.\n"
        "- Long-read sources should get meatier treatment: say what the article is about "
        "and work in a few of its key points.\n"
        "- Short news items should be compressed and woven together when related.\n"
        "- Use one markdown link per source when possible. Avoid linking the same source "
        "multiple times unless a long read truly needs a second mention.\n"
        "- For short news, aim for roughly 18-35 words of coverage per source. For long "
        "reads, aim for roughly 45-90 words.\n"
        "- Do not use bullet lists, numbered lists, markdown headings, section titles, "
        "datelines, or named segments inside chunk markdown.\n\n"
        "Chunking:\n"
        "- Return stable chunks for rendering and scroll-read tracking, but treat them as "
        "invisible storage units.\n"
        "- The reader should experience the chunk markdown joined together as one continuous "
        "briefing body.\n"
        "- Each chunk should include source_refs for the sources whose links appear in that "
        "chunk.\n"
        "- Cover every source. Do not omit sources for being niche, weak, old, or less "
        "important. Use omitted_sources only for exact duplicates whose facts are already "
        "covered by another linked source.\n"
        "- Keep source_refs compact. key_points_used may be empty for short news items.\n"
        "- Group related sources into compact paragraphs. A paragraph may contain several "
        "source links when that improves density.\n\n"
        "Semantic tap targets:\n"
        "- Mark 8-16 interesting sentence fragments across the briefing with inline markers "
        "like {{insight:short_stable_id}}the exact passage{{/insight}}.\n"
        "- Mark useful semantic chunks, not whole paragraphs: causal links, surprising "
        "numbers, strategic shifts, unresolved tensions, or concepts worth digging into.\n"
        "- Every inline insight id must have a matching insights entry on the same chunk. "
        "Do not create an insights entry unless the markdown contains the matching marker.\n"
        "- Marked passages may include markdown source links if the link is part of the "
        "interesting phrase. Do not nest insight markers.\n\n"
        "Input JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


class LlmCallTimeoutError(RuntimeError):
    """Raised when an LLM call exceeds the prototype timeout."""


@contextmanager
def llm_call_timeout(seconds: int, label: str):
    """Interrupt long-running LLM calls in this foreground prototype."""
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


def run_generation(*, model_spec: str, prompt: str, timeout_seconds: int) -> Any:
    """Run the LLM with strict structured output."""
    system_prompt = (
        "You are an editorial news briefing writer for Newsly. Produce grounded, "
        "source-linked prose matching the provided structured output schema. Do not invent "
        "facts, URLs, source IDs, or unsupported causal claims."
    )
    agent = get_basic_agent(model_spec, GeneratedUnreadBriefing, system_prompt)
    with llm_call_timeout(timeout_seconds, "one-shot briefing generation"):
        return agent.run_sync(prompt)


def run_windowed_generation(
    *,
    model_spec: str,
    user_id: int,
    sources: list[SourceItem],
    window_size: int,
    timeout_seconds: int,
    skip_repair_llm: bool,
) -> tuple[GeneratedUnreadBriefing, dict[str, int | None] | None]:
    """Generate stable invisible prose windows and join them as one briefing."""
    normalized_window_size = max(1, window_size)
    chunks: list[GeneratedBriefingChunk] = []
    usage: dict[str, int | None] | None = None
    previous_tail = ""
    total_windows = (len(sources) + normalized_window_size - 1) // normalized_window_size

    for window_index, start in enumerate(range(0, len(sources), normalized_window_size), start=1):
        window_sources = sources[start : start + normalized_window_size]
        print(
            f"Generating briefing window {window_index}/{total_windows} "
            f"({len(window_sources)} sources)...",
            file=sys.stderr,
            flush=True,
        )
        try:
            result = run_chunk_generation(
                model_spec=model_spec,
                timeout_seconds=timeout_seconds,
                label=f"briefing window {window_index}/{total_windows}",
                prompt=build_window_generation_prompt(
                    user_id=user_id,
                    sources=window_sources,
                    all_sources=sources,
                    start_index=start,
                    window_index=window_index,
                    total_windows=total_windows,
                    previous_tail=previous_tail,
                ),
            )
            chunk = result.output.model_copy(update={"chunk_index": len(chunks) + 1})
            usage = merge_usage(usage, extract_usage_from_result(result))
        except Exception as exc:  # noqa: BLE001
            print(
                f"WARNING: Window generation failed ({exc}); using deterministic window fallback.",
                file=sys.stderr,
                flush=True,
            )
            chunk = build_fallback_repair_chunk(
                sources=window_sources,
                chunk_index=len(chunks) + 1,
            )
        chunks.append(chunk)
        previous_tail = strip_insight_markers(chunk.markdown)[-1200:]

        missing_keys = sorted(
            {source.source_key for source in window_sources}
            - source_keys_from_markdown(chunk.markdown)
        )
        if missing_keys:
            print(
                f"Repairing window {window_index}/{total_windows} "
                f"({len(missing_keys)} missing sources)...",
                file=sys.stderr,
                flush=True,
            )
            missing_sources = [
                source for source in window_sources if source.source_key in set(missing_keys)
            ]
            if skip_repair_llm:
                print(
                    "Skipping repair LLM; using deterministic repair fallback.",
                    file=sys.stderr,
                    flush=True,
                )
                repair_chunk = build_fallback_repair_chunk(
                    sources=missing_sources,
                    chunk_index=len(chunks) + 1,
                )
            else:
                try:
                    repair_result = run_chunk_generation(
                        model_spec=model_spec,
                        timeout_seconds=timeout_seconds,
                        label=f"repair window {window_index}/{total_windows}",
                        prompt=build_window_repair_prompt(
                            user_id=user_id,
                            sources=missing_sources,
                            previous_tail=previous_tail,
                        ),
                    )
                    repair_chunk = repair_result.output.model_copy(
                        update={"chunk_index": len(chunks) + 1}
                    )
                    usage = merge_usage(usage, extract_usage_from_result(repair_result))
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"WARNING: Repair generation failed ({exc}); "
                        "using deterministic repair fallback.",
                        file=sys.stderr,
                        flush=True,
                    )
                    repair_chunk = build_fallback_repair_chunk(
                        sources=missing_sources,
                        chunk_index=len(chunks) + 1,
                    )
            chunks.append(repair_chunk)
            previous_tail = strip_insight_markers(repair_chunk.markdown)[-1200:]

    return (
        GeneratedUnreadBriefing(
            title=f"Unread briefing for user {user_id}",
            deck=f"{len(sources)} unread sources joined into one continuous source-linked body.",
            through_line=(
                "Generated in stable invisible windows so the rendered document reads "
                "as one briefing while preserving source coverage."
            ),
            chunks=chunks,
            omitted_sources=[],
        ),
        usage,
    )


def build_window_generation_prompt(
    *,
    user_id: int,
    sources: list[SourceItem],
    all_sources: list[SourceItem],
    start_index: int,
    window_index: int,
    total_windows: int,
    previous_tail: str,
) -> str:
    """Build a bounded prompt for one invisible prose window."""
    source_keys = [source.source_key for source in sources]
    previous_titles = [
        source.original_title for source in all_sources[max(0, start_index - 6) : start_index]
    ]
    next_offset = start_index + len(sources)
    next_titles = [source.original_title for source in all_sources[next_offset : next_offset + 6]]
    payload = {
        "user_id": user_id,
        "window": {
            "index": window_index,
            "total": total_windows,
            "source_keys_that_must_appear": source_keys,
            "previous_tail": previous_tail,
            "recent_previous_titles": previous_titles,
            "upcoming_titles_for_context_only": next_titles,
        },
        "sources": [source.to_prompt_dict() for source in sources],
    }
    return (
        "Write the next invisible storage chunk of one continuous Newsly unread briefing.\n\n"
        "Rules:\n"
        "- The rendered chunks will be joined together with no visible headings or dividers.\n"
        "- Continue naturally from previous_tail when present, but do not say you are "
        "continuing, changing sections, or moving to a new topic.\n"
        "- Include every source in source_keys_that_must_appear exactly once as a markdown "
        "link using its exact link_url.\n"
        "- Do not link recent_previous_titles or upcoming_titles_for_context_only; they are "
        "only for continuity.\n"
        "- Make each link label resemble the source title. Light cleanup is fine; generic "
        "labels are not.\n"
        "- Integrate title-like links into the sentence instead of placing them at the end "
        "as parenthetical citations. Prefer '[Title](link) argues/reports/shows...' or "
        "'...as [Title](link) describes.'\n"
        "- Keep the prose useful and dense, but not facts-only. Include concrete details "
        "where available and use brief framing or texture when that makes the item worth "
        "reading.\n"
        "- Short news items should usually get one compact clause. Long-read items should "
        "get a meatier sentence or two using several key points.\n"
        "- Mark 4-8 interesting sentence fragments with inline markers like "
        "{{insight:short_stable_id}}the exact passage{{/insight}}.\n"
        "- Every inline insight id must have a matching insights entry. Use the insights "
        "entry to explain what the reader can dig into or learn next.\n"
        "- Insight passages should be short semantic chunks: a surprising number, causal "
        "connection, strategic shift, contradiction, or technical concept. Do not mark "
        "entire paragraphs or nest markers.\n"
        "- No headings, bullets, numbered lists, datelines, or named segments.\n"
        "- Keep source_refs compact; key_points_used may be empty for short news items.\n\n"
        "Input JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def build_window_repair_prompt(
    *,
    user_id: int,
    sources: list[SourceItem],
    previous_tail: str,
) -> str:
    """Build a continuation prompt for sources missed by a generated window."""
    payload = {
        "user_id": user_id,
        "previous_tail": previous_tail,
        "source_keys_that_must_appear": [source.source_key for source in sources],
        "sources": [source.to_prompt_dict() for source in sources],
    }
    return (
        "Write a dense continuation paragraph for the same continuous Newsly briefing.\n\n"
        "Rules:\n"
        "- Do not mention that this is a repair, catch-up, omission, appendix, or addendum.\n"
        "- Include every source in source_keys_that_must_appear exactly once as a markdown "
        "link using its exact link_url.\n"
        "- Make link labels resemble the source titles.\n"
        "- Integrate source links into the sentence instead of using citation-style "
        "parentheses.\n"
        "- Add 0-1 inline insight markers only if there is a genuinely useful semantic "
        "chunk to highlight, and include matching insight metadata.\n"
        "- Continue from previous_tail without headings, bullets, numbered lists, datelines, "
        "or named segments.\n\n"
        "Input JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def run_chunk_generation(
    *,
    model_spec: str,
    prompt: str,
    timeout_seconds: int,
    label: str,
) -> Any:
    """Run the LLM for one stable invisible chunk."""
    system_prompt = (
        "You are an editorial news briefing writer for Newsly. Produce grounded, "
        "source-linked continuous prose matching the provided structured output schema. "
        "Do not invent facts, URLs, source IDs, or unsupported causal claims."
    )
    agent = get_basic_agent(model_spec, GeneratedBriefingChunk, system_prompt)
    with llm_call_timeout(timeout_seconds, label):
        return agent.run_sync(prompt)


def build_fallback_repair_chunk(
    *,
    sources: list[SourceItem],
    chunk_index: int,
) -> GeneratedBriefingChunk:
    """Build a deterministic source-linked continuation for timed-out repairs."""
    sentences: list[str] = []
    source_refs: list[GeneratedBriefingSourceRef] = []
    connectors = [
        "",
        "Nearby, ",
        "That sits beside ",
        "The same unread stack also has ",
        "For context, ",
    ]
    for index, source in enumerate(sources):
        point = source.summary or next(iter(source.key_points), None) or "is worth reviewing"
        label = source.original_title.replace("[", "").replace("]", "")
        linked_title = f"[{label}]({source.link_url})"
        connector = connectors[index % len(connectors)]
        if index == 0:
            sentence = f"{linked_title} says {point}"
        elif connector.endswith(" "):
            sentence = f"{connector}{linked_title}, where {point}"
        else:
            sentence = f"{connector}{linked_title} says {point}"
        if not sentence.endswith((".", "!", "?")):
            sentence += "."
        sentences.append(sentence)
        source_refs.append(
            GeneratedBriefingSourceRef(
                source_key=source.source_key,
                generated_title=source.original_title,
                role="repair fallback",
                key_points_used=source.key_points[:1],
            )
        )
    return GeneratedBriefingChunk(
        chunk_index=chunk_index,
        markdown=" ".join(sentences),
        source_refs=source_refs,
        insights=[],
    )


def merge_usage(
    left: dict[str, int | None] | None,
    right: dict[str, int | None] | None,
) -> dict[str, int | None] | None:
    """Sum two usage dictionaries while preserving unknown values."""
    if not left:
        return right
    if not right:
        return left
    keys = set(left) | set(right)
    merged: dict[str, int | None] = {}
    for key in keys:
        left_value = left.get(key)
        right_value = right.get(key)
        if left_value is None and right_value is None:
            merged[key] = None
        else:
            merged[key] = int(left_value or 0) + int(right_value or 0)
    return merged


def strip_insight_markers(text: str) -> str:
    """Remove semantic-span markers from generated prose."""
    without_starts = INSIGHT_START_RE.sub("", text)
    return without_starts.replace(INSIGHT_END_MARKER, "")


def normalize_generated_briefing(
    briefing: GeneratedUnreadBriefing,
) -> tuple[GeneratedUnreadBriefing, list[str]]:
    """Drop stale semantic metadata that cannot be opened from inline prose."""
    chunks: list[GeneratedBriefingChunk] = []
    warnings: list[str] = []
    dropped_ids: list[str] = []
    stripped_marker_ids: list[str] = []
    repaired_end_markers = 0
    repaired_start_markers = 0
    renamed_ids: list[str] = []
    seen_ids: set[str] = set()
    for chunk in briefing.chunks:
        markdown = chunk.markdown
        if "[/insight]" in markdown:
            repaired_end_markers += markdown.count("[/insight]")
            markdown = markdown.replace("[/insight]", INSIGHT_END_MARKER)
        if "[[insight:" in markdown:
            repaired_start_markers += markdown.count("[[insight:")
            markdown = re.sub(
                r"\[\[insight:([A-Za-z0-9_-]+)\]\]",
                r"{{insight:\1}}",
                markdown,
            )
        markdown, id_renames = rename_duplicate_insight_markers(
            markdown=markdown,
            chunk_index=chunk.chunk_index,
            seen_ids=seen_ids,
        )
        renamed_ids.extend(f"{old}->{new}" for old, new in id_renames.items())
        marker_ids = set(insight_marker_ids_from_markdown(markdown))
        filtered_insights = [
            insight.model_copy(update={"insight_id": id_renames.get(insight.insight_id)})
            if insight.insight_id in id_renames
            else insight
            for insight in chunk.insights
            if id_renames.get(insight.insight_id, insight.insight_id) in marker_ids
        ]
        dropped_ids.extend(
            insight.insight_id
            for insight in chunk.insights
            if id_renames.get(insight.insight_id, insight.insight_id) not in marker_ids
        )
        markdown, stripped_ids = strip_undeclared_insight_markers(
            markdown=markdown,
            declared_ids={insight.insight_id for insight in filtered_insights},
        )
        stripped_marker_ids.extend(stripped_ids)
        if (
            markdown == chunk.markdown
            and len(filtered_insights) == len(chunk.insights)
            and not id_renames
        ):
            chunks.append(chunk)
        else:
            chunks.append(
                chunk.model_copy(update={"markdown": markdown, "insights": filtered_insights})
            )

    if repaired_end_markers:
        warnings.append(f"Repaired {repaired_end_markers} malformed insight end markers.")
    if repaired_start_markers:
        warnings.append(f"Repaired {repaired_start_markers} malformed insight start markers.")
    if renamed_ids:
        warnings.append(f"Renamed duplicate insight ids: {sorted(set(renamed_ids))}")
    if dropped_ids:
        warnings.append(
            f"Dropped insight metadata without matching inline markers: {sorted(set(dropped_ids))}"
        )
    if stripped_marker_ids:
        warnings.append(
            f"Removed insight markers without matching metadata: {sorted(set(stripped_marker_ids))}"
        )
    return briefing.model_copy(update={"chunks": chunks}), warnings


def strip_undeclared_insight_markers(
    *,
    markdown: str,
    declared_ids: set[str],
) -> tuple[str, list[str]]:
    """Remove inline semantic spans that have no matching metadata."""
    stripped_ids: list[str] = []
    pattern = re.compile(
        r"\{\{insight:([A-Za-z0-9_-]+)\}\}(.*?)\{\{/insight\}\}",
        re.DOTALL,
    )

    def replace_span(match: re.Match[str]) -> str:
        insight_id = match.group(1)
        if insight_id in declared_ids:
            return match.group(0)
        stripped_ids.append(insight_id)
        return match.group(2)

    return pattern.sub(replace_span, markdown), stripped_ids


def rename_duplicate_insight_markers(
    *,
    markdown: str,
    chunk_index: int,
    seen_ids: set[str],
) -> tuple[str, dict[str, str]]:
    """Rename repeated insight marker ids so each rendered tap target is unique."""
    id_renames: dict[str, str] = {}
    local_seen: set[str] = set()

    def replace_marker(match: re.Match[str]) -> str:
        insight_id = match.group(1)
        if insight_id not in seen_ids and insight_id not in local_seen:
            local_seen.add(insight_id)
            seen_ids.add(insight_id)
            return match.group(0)

        new_id = unique_insight_id(insight_id, chunk_index, seen_ids | local_seen)
        local_seen.add(new_id)
        seen_ids.add(new_id)
        id_renames[insight_id] = new_id
        return f"{{{{insight:{new_id}}}}}"

    return INSIGHT_START_RE.sub(replace_marker, markdown), id_renames


def unique_insight_id(base_id: str, chunk_index: int, existing_ids: set[str]) -> str:
    """Return a deterministic unique semantic insight id."""
    candidate = f"{base_id}_{chunk_index}"
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{base_id}_{chunk_index}_{suffix}"
        suffix += 1
    return candidate


def validate_generated_sources(
    briefing: GeneratedUnreadBriefing,
    sources: list[SourceItem],
) -> list[str]:
    """Return warnings for source-link and semantic-insight issues."""
    available = {source.source_key for source in sources}
    linked: set[str] = set()
    source_ref_keys: set[str] = set()
    unknown: set[str] = set()
    malformed_source_links: set[str] = set()
    missing_insight_metadata: set[str] = set()
    unused_insight_metadata: set[str] = set()
    unknown_insight_sources: set[str] = set()
    duplicate_insight_ids: set[str] = set()
    seen_insight_ids: set[str] = set()
    unbalanced_insight_chunks: list[int] = []
    for chunk in briefing.chunks:
        malformed_source_links.update(invalid_source_link_urls_from_markdown(chunk.markdown))
        for ref in chunk.source_refs:
            if ref.source_key in available:
                source_ref_keys.add(ref.source_key)
            else:
                unknown.add(ref.source_key)
        for key in source_keys_from_markdown(chunk.markdown):
            if key in available:
                linked.add(key)
            else:
                unknown.add(key)

        marker_ids = insight_marker_ids_from_markdown(chunk.markdown)
        marker_id_set = set(marker_ids)
        declared_ids = {insight.insight_id for insight in chunk.insights}
        missing_insight_metadata.update(marker_id_set - declared_ids)
        unused_insight_metadata.update(declared_ids - marker_id_set)
        for insight in chunk.insights:
            if insight.insight_id in seen_insight_ids:
                duplicate_insight_ids.add(insight.insight_id)
            seen_insight_ids.add(insight.insight_id)
            unknown_insight_sources.update(
                source_key for source_key in insight.source_keys if source_key not in available
            )
        if len(marker_ids) != chunk.markdown.count(INSIGHT_END_MARKER):
            unbalanced_insight_chunks.append(chunk.chunk_index)

    omitted = {omission.source_key for omission in briefing.omitted_sources}
    missing = sorted(available - linked - omitted)
    source_refs_without_links = sorted(source_ref_keys - linked)
    warnings: list[str] = []
    if missing:
        warnings.append(
            f"Generated briefing did not include clickable links for {len(missing)} "
            f"sources: {missing}"
        )
    if source_refs_without_links:
        warnings.append(
            f"Generated source_refs without matching markdown links: {source_refs_without_links}"
        )
    if malformed_source_links:
        warnings.append(
            f"Generated malformed newsly source links: {sorted(malformed_source_links)}"
        )
    if unknown:
        warnings.append(f"Generated briefing referenced unknown sources: {sorted(unknown)}")
    if missing_insight_metadata:
        warnings.append(
            "Generated insight markers without matching metadata: "
            f"{sorted(missing_insight_metadata)}"
        )
    if unused_insight_metadata:
        warnings.append(
            "Generated insight metadata without matching inline markers: "
            f"{sorted(unused_insight_metadata)}"
        )
    if unknown_insight_sources:
        warnings.append(
            "Generated insight metadata referenced unknown sources: "
            f"{sorted(unknown_insight_sources)}"
        )
    if duplicate_insight_ids:
        warnings.append(f"Generated duplicate insight ids: {sorted(duplicate_insight_ids)}")
    if unbalanced_insight_chunks:
        warnings.append(
            f"Generated unbalanced insight markers in chunks: {sorted(unbalanced_insight_chunks)}"
        )
    return warnings


def source_link_urls_from_markdown(markdown: str) -> list[str]:
    """Extract raw prototype source URLs from markdown links."""
    return re.findall(r"\]\((newsly://briefing/[^)]+)\)", markdown)


def invalid_source_link_urls_from_markdown(markdown: str) -> list[str]:
    """Return prototype source URLs that do not map to a source key."""
    return [
        raw_url
        for raw_url in source_link_urls_from_markdown(markdown)
        if source_key_from_link_url(raw_url) is None
    ]


def source_keys_from_markdown(markdown: str) -> set[str]:
    """Extract source keys from newsly markdown links."""
    keys: set[str] = set()
    for raw_url in source_link_urls_from_markdown(markdown):
        key = source_key_from_link_url(raw_url)
        if key:
            keys.add(key)
    return keys


def insight_marker_ids_from_markdown(markdown: str) -> list[str]:
    """Extract inline semantic insight marker ids from markdown."""
    return INSIGHT_START_RE.findall(markdown)


def source_key_from_link_url(url: str) -> str | None:
    """Convert a prototype newsly URL back to a source key."""
    match = re.match(r"^newsly://briefing/(news|content)/(\d+)$", url.strip())
    if not match:
        return None
    kind, target_id = match.groups()
    return f"{'news' if kind == 'news' else 'content'}:{target_id}"


def build_output_payload(
    *,
    args: argparse.Namespace,
    database_url: str,
    source_meta: dict[str, Any],
    sources: list[SourceItem],
    selection_result: BriefingSourceSelectionResult | None,
    prompt_path: Path,
    briefing: GeneratedUnreadBriefing | None,
    usage: dict[str, int | None] | None,
    estimated_cost_usd: float | None,
    warnings: list[str],
) -> dict[str, Any]:
    """Build the persisted JSON artifact."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "user_id": args.user_id,
        "database": redact_database_url(database_url),
        "model": None if args.skip_llm else args.model,
        "source_meta": source_meta,
        "source_count": len(sources),
        "sources": [source.to_manifest_dict() for source in sources],
        "selection": selection_result.model_dump(mode="json") if selection_result else None,
        "read_manifest": build_read_manifest(sources),
        "prompt_path": str(prompt_path),
        "briefing": briefing.model_dump(mode="json") if briefing else None,
        "usage": usage,
        "estimated_cost_usd": estimated_cost_usd,
        "warnings": warnings,
    }


def redact_database_url(database_url: str) -> str:
    """Hide passwords in persisted artifacts."""
    parsed = urlparse(database_url)
    if parsed.password is None:
        return database_url
    netloc = parsed.hostname or ""
    if parsed.username:
        netloc = f"{quote(parsed.username, safe='')}@{netloc}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def build_read_manifest(sources: list[SourceItem]) -> dict[str, Any]:
    """Return IDs and endpoints a client would use after scroll exposure."""
    news_ids = [source.target_id for source in sources if source.source_key.startswith("news:")]
    content_ids = [
        source.target_id for source in sources if source.source_key.startswith("content:")
    ]
    return {
        "policy": "mark_after_user_scrolls_past_source_link",
        "news": {
            "endpoint": "POST /api/news/items/mark-read",
            "request_body": {"content_ids": news_ids},
        },
        "long_form": {
            "endpoint": "POST /api/content/bulk-mark-read",
            "request_body": {"content_ids": content_ids},
        },
    }


def render_markdown(briefing: GeneratedUnreadBriefing, sources: list[SourceItem]) -> str:
    """Render a readable Markdown artifact."""
    del sources
    lines = [f"# {briefing.title}", ""]
    for chunk in briefing.chunks:
        lines.extend([strip_insight_markers(chunk.markdown.strip()), ""])
    if briefing.omitted_sources:
        lines.extend(["## Omitted Sources", ""])
        for omitted in briefing.omitted_sources:
            lines.append(f"- `{omitted.source_key}`: {omitted.reason}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_html(
    briefing: GeneratedUnreadBriefing,
    sources: list[SourceItem],
    payload: dict[str, Any],
) -> str:
    """Render an interactive HTML preview with expandable source links."""
    source_map = {source.source_key: source.to_manifest_dict() for source in sources}
    insight_map = {
        insight.insight_id: insight.model_dump(mode="json")
        for chunk in briefing.chunks
        for insight in chunk.insights
    }
    source_json = json.dumps(source_map, ensure_ascii=False).replace("</", "<\\/")
    insight_json = json.dumps(insight_map, ensure_ascii=False).replace("</", "<\\/")
    payload_json = json.dumps(
        {
            "generated_at": payload["generated_at"],
            "model": payload["model"],
            "usage": payload["usage"],
            "estimated_cost_usd": payload["estimated_cost_usd"],
            "warnings": payload["warnings"],
        },
        ensure_ascii=False,
        default=str,
    ).replace("</", "<\\/")
    chunks_html = "\n".join(render_chunk_html(chunk) for chunk in briefing.chunks)
    source_count_text = f"{payload['source_count']} sources"
    omitted_html = ""
    if briefing.omitted_sources:
        items = "".join(
            f"<li><code>{html.escape(item.source_key)}</code>: {html.escape(item.reason)}</li>"
            for item in briefing.omitted_sources
        )
        omitted_html = (
            '<details class="diagnostics"><summary>Omitted sources</summary>'
            f"<ul>{items}</ul></details>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(briefing.title)}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #fbfbfa;
      --ink: #1f1f1d;
      --muted: #6b6a66;
      --line: #d9d8d4;
      --panel: #ffffff;
      --accent: #2f6f4e;
      --insight-bg: rgba(194, 136, 31, .16);
      --insight-active: rgba(194, 136, 31, .28);
      --insight-line: #9c6b1f;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #171817;
        --ink: #f1f0ec;
        --muted: #aaa8a0;
        --line: #383a36;
        --panel: #222420;
        --accent: #8cc7a2;
        --insight-bg: rgba(207, 166, 83, .18);
        --insight-active: rgba(207, 166, 83, .30);
        --insight-line: #cfa653;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 17px/1.6 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
    }}
    main {{
      width: min(840px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 34px 0 96px;
    }}
    header {{
      border-bottom: 1px solid var(--line);
      margin-bottom: 24px;
      padding-bottom: 16px;
    }}
    h1 {{
      line-height: 1.12;
      letter-spacing: 0;
      font-size: 1.45rem;
      margin: 0 0 8px;
      max-width: 680px;
    }}
    .meta {{ color: var(--muted); font-size: 0.86rem; margin: 0; }}
    article {{ max-width: 780px; }}
    .chunk {{
      margin: 0;
      padding: 0;
    }}
    .chunk p {{ margin: 0 0 1rem; }}
    a.source-link {{
      color: var(--accent);
      text-decoration-thickness: 1px;
      text-underline-offset: 3px;
      cursor: pointer;
    }}
    a.source-link.seen {{ text-decoration-style: dashed; }}
    .semantic-hit {{
      background: var(--insight-bg);
      box-shadow: inset 0 -1px var(--insight-line);
      cursor: pointer;
    }}
    .semantic-hit.active {{
      background: var(--insight-active);
      outline: 1px solid var(--insight-line);
      outline-offset: 2px;
    }}
    .semantic-hit:focus-visible {{
      outline: 2px solid var(--insight-line);
      outline-offset: 2px;
    }}
    body.sheet-open {{ overflow: hidden; }}
    .sheet-backdrop {{
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,.30);
      opacity: 0;
      transition: opacity 160ms ease;
      z-index: 20;
    }}
    .sheet-backdrop.open {{ opacity: 1; }}
    .bottom-sheet {{
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
    }}
    .bottom-sheet.open {{
      transform: translate(-50%, var(--sheet-drag-y));
    }}
    .bottom-sheet.dragging {{ transition: none; }}
    .sheet-top {{
      flex: 0 0 auto;
      padding: 8px 16px 10px;
      border-bottom: 1px solid var(--line);
      touch-action: none;
      cursor: grab;
    }}
    .sheet-grabber {{
      width: 38px;
      height: 4px;
      border-radius: 2px;
      background: var(--line);
      margin: 0 auto 8px;
    }}
    .sheet-actions {{
      display: flex;
      justify-content: flex-end;
    }}
    .sheet-close {{
      appearance: none;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: transparent;
      color: var(--ink);
      cursor: pointer;
      font: inherit;
      font-size: .88rem;
      line-height: 1;
      padding: 7px 10px;
    }}
    .sheet-content {{
      overflow: auto;
      padding: 18px 18px 28px;
      -webkit-overflow-scrolling: touch;
    }}
    .sheet-title {{
      margin: 0 0 8px;
      font-size: 1.08rem;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }}
    .sheet-meta {{
      color: var(--muted);
      font-size: .86rem;
      line-height: 1.4;
      margin: 0 0 12px;
    }}
    .sheet-content p {{ margin: 0 0 12px; }}
    .sheet-content ul {{ margin: 8px 0 14px 20px; padding: 0; }}
    .sheet-content li {{ margin-bottom: 5px; }}
    .sheet-content a {{ color: var(--accent); }}
    .insight-source-list {{
      color: var(--muted);
      font-size: .88rem;
      margin-top: 14px;
    }}
    .status {{
      position: fixed;
      right: 14px;
      bottom: 14px;
      width: min(320px, calc(100vw - 28px));
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 14px;
      color: var(--muted);
      font: 0.8rem/1.35 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
      box-shadow: 0 12px 30px rgba(0,0,0,.12);
    }}
    .status strong {{ color: var(--ink); }}
    .diagnostics {{
      border-top: 1px solid var(--line);
      color: var(--muted);
      margin-top: 34px;
      padding-top: 18px;
      font-size: 0.92rem;
    }}
    .diagnostics summary {{ cursor: pointer; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{html.escape(briefing.title)}</h1>
      <p class="meta">{html.escape(source_count_text)} · {html.escape(str(payload["model"]))}</p>
    </header>
    <article>
      {chunks_html}
    </article>
    {omitted_html}
  </main>
  <aside class="status">
    <strong>Scroll-read simulation</strong><br>
    Links are marked "seen" only after you scroll past them. No API calls are made.
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
    const SOURCE_MAP = {source_json};
    const INSIGHT_MAP = {insight_json};
    const RUN_META = {payload_json};
    const seen = new Set();

    function keyPointsList(source) {{
      const points = source.key_points || [];
      if (!points.length) return "";
      return `<ul>${{points.map((point) => `<li>${{escapeHtml(point)}}</li>`).join("")}}</ul>`;
    }}

    const sheet = document.getElementById("bottom-sheet");
    const sheetContent = document.getElementById("sheet-content");
    const sheetBackdrop = document.getElementById("sheet-backdrop");
    const sheetClose = document.getElementById("sheet-close");
    const sheetDragHandle = document.getElementById("sheet-drag-handle");
    let activeSemanticNode = null;
    let dragStartY = null;
    let dragPointerId = null;
    let dragDeltaY = 0;

    function sourceSheetContent(sourceKey) {{
      const source = SOURCE_MAP[sourceKey];
      if (!source) return null;
      const external = source.url ? `<p><a href="${{escapeAttr(source.url)}}" target="_blank" rel="noreferrer">Open original</a></p>` : "";
      const summary = source.summary ? `<p>${{escapeHtml(source.summary)}}</p>` : "";
      return `<div class="source-detail" data-sheet-for="${{escapeAttr(sourceKey)}}">
        <h2 class="sheet-title">${{escapeHtml(source.original_title)}}</h2>
        <div class="sheet-meta">${{escapeHtml(source.source_name || source.kind)}} · ${{escapeHtml(source.published_at || "undated")}} · <code>${{escapeHtml(sourceKey)}}</code></div>
        ${{summary}}
        ${{keyPointsList(source)}}
        ${{external}}
      </div>`;
    }}

    function insightSheetContent(insightId) {{
      const insight = INSIGHT_MAP[insightId];
      if (!insight) return null;
      const sources = (insight.source_keys || [])
        .map((sourceKey) => SOURCE_MAP[sourceKey]?.original_title || sourceKey)
        .filter(Boolean);
      const sourceLine = sources.length
        ? `<div class="insight-source-list">Sources: ${{sources.map(escapeHtml).join("; ")}}</div>`
        : "";
      const questions = insight.follow_up_questions || [];
      const questionList = questions.length
        ? `<ul>${{questions.map((question) => `<li>${{escapeHtml(question)}}</li>`).join("")}}</ul>`
        : "";
      return `<div class="insight-detail" data-sheet-for="${{escapeAttr(insightId)}}">
        <h2 class="sheet-title">${{escapeHtml(insight.title || "Learn more")}}</h2>
        <p>${{escapeHtml(insight.learn_more || "")}}</p>
        ${{sourceLine}}
        ${{questionList}}
      </div>`;
    }}

    function openSheet(contentHtml) {{
      if (!contentHtml) return;
      sheetContent.innerHTML = contentHtml;
      sheet.style.setProperty("--sheet-drag-y", "0px");
      sheetBackdrop.hidden = false;
      requestAnimationFrame(() => {{
        sheetBackdrop.classList.add("open");
        sheet.classList.add("open");
      }});
      sheet.setAttribute("aria-hidden", "false");
      document.body.classList.add("sheet-open");
    }}

    function closeSheet() {{
      sheetBackdrop.classList.remove("open");
      sheet.classList.remove("open");
      sheet.classList.remove("dragging");
      sheet.setAttribute("aria-hidden", "true");
      document.body.classList.remove("sheet-open");
      sheet.style.setProperty("--sheet-drag-y", "0px");
      clearActiveInsight();
      window.setTimeout(() => {{
        if (!sheet.classList.contains("open")) {{
          sheetBackdrop.hidden = true;
          sheetContent.innerHTML = "";
        }}
      }}, 190);
    }}

    function clearActiveInsight() {{
      if (activeSemanticNode) {{
        activeSemanticNode.classList.remove("active");
      }}
      activeSemanticNode = null;
      document.querySelectorAll(".semantic-hit.active").forEach((node) => {{
        node.classList.remove("active");
      }});
    }}

    function escapeHtml(value) {{
      return String(value || "").replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\\"": "&quot;",
        "'": "&#39;"
      }}[char]));
    }}

    function escapeAttr(value) {{
      return escapeHtml(value).replace(/`/g, "&#96;");
    }}

    document.addEventListener("click", (event) => {{
      const link = event.target.closest("a.source-link");
      if (link) {{
        event.preventDefault();
        const sourceKey = link.dataset.sourceKey;
        clearActiveInsight();
        openSheet(sourceSheetContent(sourceKey));
        return;
      }}

      const insightTarget = event.target.closest(".semantic-hit");
      if (!insightTarget) return;
      event.preventDefault();
      openInsight(insightTarget);
    }});

    document.addEventListener("keydown", (event) => {{
      if (event.key === "Escape" && sheet.classList.contains("open")) {{
        closeSheet();
        return;
      }}
      if (event.key !== "Enter" && event.key !== " ") return;
      const insightTarget = event.target.closest(".semantic-hit");
      if (!insightTarget || event.target.closest("a.source-link")) return;
      event.preventDefault();
      openInsight(insightTarget);
    }});

    function openInsight(target) {{
      const insightId = target.dataset.insightId;
      if (!insightId) return;
      const content = insightSheetContent(insightId);
      if (!content) return;
      clearActiveInsight();
      target.classList.add("active");
      activeSemanticNode = target;
      openSheet(content);
    }}

    sheetBackdrop.addEventListener("click", closeSheet);
    sheetClose.addEventListener("click", closeSheet);
    sheetDragHandle.addEventListener("pointerdown", (event) => {{
      if (!sheet.classList.contains("open")) return;
      if (event.target.closest("button, a")) return;
      dragStartY = event.clientY;
      dragPointerId = event.pointerId;
      dragDeltaY = 0;
      sheet.classList.add("dragging");
      sheetDragHandle.setPointerCapture(event.pointerId);
    }});
    sheetDragHandle.addEventListener("pointermove", (event) => {{
      if (dragStartY === null || dragPointerId !== event.pointerId) return;
      dragDeltaY = Math.max(0, event.clientY - dragStartY);
      sheet.style.setProperty("--sheet-drag-y", `${{dragDeltaY}}px`);
    }});
    function endSheetDrag(event) {{
      if (dragStartY === null || dragPointerId !== event.pointerId) return;
      sheet.classList.remove("dragging");
      dragStartY = null;
      dragPointerId = null;
      if (dragDeltaY > 90) {{
        closeSheet();
      }} else {{
        sheet.style.setProperty("--sheet-drag-y", "0px");
      }}
      dragDeltaY = 0;
    }}
    sheetDragHandle.addEventListener("pointerup", endSheetDrag);
    sheetDragHandle.addEventListener("pointercancel", endSheetDrag);

    function updateSeen() {{
      document.querySelectorAll("a.source-link").forEach((link) => {{
        const rect = link.getBoundingClientRect();
        if (rect.bottom < window.innerHeight * 0.18) {{
          seen.add(link.dataset.sourceKey);
          link.classList.add("seen");
        }}
      }});
      document.getElementById("seen-count").textContent = `${{seen.size}} sources seen`;
    }}

    document.addEventListener("scroll", updateSeen, {{ passive: true }});
    updateSeen();
  </script>
</body>
</html>
"""


def render_chunk_html(chunk: GeneratedBriefingChunk) -> str:
    """Render one generated chunk to HTML."""
    return (
        f'<section class="chunk" data-chunk-index="{chunk.chunk_index}">'
        f"{markdown_to_html(chunk.markdown)}"
        "</section>"
    )


def markdown_to_html(markdown: str) -> str:
    """Render a narrow Markdown subset used by the model."""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", markdown.strip()) if block.strip()]
    rendered: list[str] = []
    for block in blocks:
        block = re.sub(r"^#{1,6}\s+", "", block)
        rendered.append(f"<p>{render_inline_markdown(block)}</p>")
    return "\n".join(rendered)


def render_inline_markdown(text: str) -> str:
    """Render links, semantic spans, and light emphasis without a Markdown dependency."""
    parts: list[str] = []
    last_end = 0
    open_insights = 0
    token_re = re.compile(
        r"(\{\{insight:([A-Za-z0-9_-]+)\}\}|\{\{/insight\}\}|\[([^\]]+)\]\(([^)]+)\))"
    )
    for match in token_re.finditer(text):
        parts.append(html.escape(text[last_end : match.start()]))
        token = match.group(1)
        insight_id = match.group(2)
        link_label = match.group(3)
        link_url = match.group(4)

        if insight_id:
            parts.append(
                '<span class="semantic-hit" role="button" tabindex="0" '
                f'data-insight-id="{html.escape(insight_id)}">'
            )
            open_insights += 1
        elif token == INSIGHT_END_MARKER:
            if open_insights:
                parts.append("</span>")
                open_insights -= 1
        elif link_label is not None and link_url is not None:
            label = html.escape(link_label.strip())
            url = link_url.strip()
            source_key = source_key_from_link_url(url)
            if source_key:
                parts.append(
                    '<a class="source-link" href="#" '
                    f'data-source-key="{html.escape(source_key)}">{label}</a>'
                )
            else:
                parts.append(
                    f'<a href="{html.escape(url)}" target="_blank" rel="noreferrer">{label}</a>'
                )
        else:
            parts.append(html.escape(token))
        last_end = match.end()
    parts.append(html.escape(text[last_end:]))
    if open_insights:
        parts.extend("</span>" for _ in range(open_insights))
    rendered = "".join(parts)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)


if __name__ == "__main__":
    raise SystemExit(main())
