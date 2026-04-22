"""Generate and persist long-form insight reports from a user's saved knowledge.

Pipeline:
1. Load the user's most-recent knowledge saves (title + summary snippets).
2. Ask an LLM to name 3-4 recurring themes across those items.
3. For each theme, run an Exa web search to surface fresh perspectives.
4. Synthesize a structured insight report that combines the user's library and
   the web findings.
5. Persist the result as a ``Content`` row + per-user inbox entry.

The task handler at ``app/pipeline/handlers/generate_insight_report.py`` drives
this end-to-end; ``scripts/generate_insight_report.py`` runs it interactively
for iteration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.contracts import ContentClassification, ContentStatus, ContentType
from app.models.schema import Content, ContentKnowledgeSave, ContentStatusEntry
from app.services.exa_client import ExaSearchResult, exa_search, get_exa_client
from app.services.llm_models import build_pydantic_model

logger = get_logger(__name__)

SYNTHESIS_MODEL = "anthropic:claude-sonnet-4-6"
SYNTHESIS_EFFORT = "high"
THEME_MODEL = "openai:gpt-5.4-mini"

MAX_KNOWLEDGE_ITEMS = 12
MAX_SUMMARY_CHARS_PER_ITEM = 1200
MAX_KEY_POINTS_PER_ITEM = 6
THEME_COUNT = 4
EXA_RESULTS_PER_THEME = 4

DEFAULT_MIN_SAVES_FOR_TRIGGER = 10


@dataclass
class KnowledgeItem:
    """Lightweight view of a saved content row for prompt assembly."""

    content_id: int
    title: str
    url: str | None
    source: str | None
    summary_text: str
    key_points: list[str]

    def to_prompt_block(self) -> str:
        source = f" — {self.source}" if self.source else ""
        points = "\n".join(f"    - {p}" for p in self.key_points) if self.key_points else ""
        body = self.summary_text.strip()
        if len(body) > MAX_SUMMARY_CHARS_PER_ITEM:
            body = body[:MAX_SUMMARY_CHARS_PER_ITEM] + "…"
        parts = [f"[#{self.content_id}] {self.title}{source}"]
        if self.url:
            parts.append(f"  URL: {self.url}")
        parts.append(f"  Summary: {body}")
        if points:
            parts.append("  Key points:")
            parts.append(points)
        return "\n".join(parts)


class ThemeList(BaseModel):
    """Themes extracted from a user's knowledge library."""

    themes: list[str] = Field(
        description="3-4 concise noun-phrase themes the user has been reading about",
        min_length=1,
        max_length=6,
    )


class DigDeeperArea(BaseModel):
    """A chat-starter the user can tap to open a follow-up conversation."""

    title: str = Field(description="Short label shown on the tap target")
    prompt: str = Field(
        description=(
            "First-person question or instruction that seeds a chat with the "
            "assistant. Written as if the reader is speaking — e.g. 'Help me "
            "compare X and Y across my saved items.'"
        )
    )


class InsightReport(BaseModel):
    """Long-form editorial report generated from a user's saved knowledge."""

    title: str
    subtitle: str | None = None
    intro: str = Field(description="2-3 sentence framing of what's happening across the library")
    themes: list[str] = Field(description="The themes that organize this report")
    insights: list[str] = Field(
        description="Non-obvious observations synthesized across multiple saved items",
        default_factory=list,
    )
    learnings: list[str] = Field(
        description="Durable, takeaway-style lessons the user seems to be converging on",
        default_factory=list,
    )
    curiosities: list[str] = Field(
        description="Interesting tensions, contradictions, or open questions worth sitting with",
        default_factory=list,
    )
    dig_deeper_areas: list[DigDeeperArea] = Field(
        description=(
            "3-5 chat-starter prompts that tap to open a follow-up conversation. "
            "Each prompt should reference specific saved items or tensions from the report."
        ),
        default_factory=list,
    )
    referenced_knowledge_ids: list[int] = Field(
        description="content_id values from the user's library that informed this report",
        default_factory=list,
    )


def load_knowledge_items(
    db: Session,
    *,
    user_id: int,
    limit: int = MAX_KNOWLEDGE_ITEMS,
) -> list[KnowledgeItem]:
    """Pull the user's most recently saved knowledge for prompt assembly."""
    stmt = (
        select(Content, ContentKnowledgeSave.saved_at)
        .join(ContentKnowledgeSave, ContentKnowledgeSave.content_id == Content.id)
        .where(ContentKnowledgeSave.user_id == user_id)
        .order_by(ContentKnowledgeSave.saved_at.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()

    items: list[KnowledgeItem] = []
    for content, _saved_at in rows:
        metadata = _coerce_metadata(content.content_metadata)
        summary_obj = metadata.get("summary")
        summary_text, key_points = _extract_summary_fields(summary_obj)
        if not summary_text and not content.title:
            continue
        items.append(
            KnowledgeItem(
                content_id=int(content.id),
                title=(content.title or "Untitled").strip(),
                url=content.url,
                source=content.source,
                summary_text=summary_text or "",
                key_points=key_points[:MAX_KEY_POINTS_PER_ITEM],
            )
        )
    return items


def _coerce_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _extract_summary_fields(summary_obj: Any) -> tuple[str, list[str]]:
    """Pull a prose summary and bullet-style key points out of the summary blob.

    Handles both the dict-shaped editorial summary used by article/podcast
    flows and the occasional plain-string summary.
    """
    if isinstance(summary_obj, str):
        return summary_obj.strip(), []
    if not isinstance(summary_obj, dict):
        return "", []

    text_parts: list[str] = []
    narrative = summary_obj.get("editorial_narrative") or summary_obj.get("narrative")
    if isinstance(narrative, str) and narrative.strip():
        text_parts.append(narrative.strip())

    details = summary_obj.get("source_details")
    if isinstance(details, dict):
        primary = details.get("primary_claim")
        if isinstance(primary, str) and primary.strip():
            text_parts.append(f"Primary claim: {primary.strip()}")

    key_points_raw = summary_obj.get("key_points") or []
    key_points: list[str] = []
    if isinstance(key_points_raw, list):
        for entry in key_points_raw:
            if isinstance(entry, str) and entry.strip():
                key_points.append(entry.strip())
            elif isinstance(entry, dict):
                point = entry.get("point") or entry.get("text")
                if isinstance(point, str) and point.strip():
                    key_points.append(point.strip())

    return "\n\n".join(text_parts), key_points


def extract_themes(knowledge_items: list[KnowledgeItem]) -> list[str]:
    """Ask the LLM to name the recurring themes in the user's saved library."""
    if not knowledge_items:
        return []

    prompt_blocks = "\n\n".join(item.to_prompt_block() for item in knowledge_items)
    system_prompt = (
        "You group a reader's saved articles into a small number of recurring "
        "themes. Prefer specific, noun-phrase themes over generic buckets. "
        "Each theme should cover multiple items from the library."
    )
    user_prompt = (
        f"Identify {THEME_COUNT} themes that best organize the following saved items. "
        "Return concise noun phrases suitable for a newsletter section heading.\n\n"
        f"{prompt_blocks}"
    )

    model, model_settings = build_pydantic_model(THEME_MODEL)
    agent: Agent[None, ThemeList] = Agent(
        model,
        output_type=ThemeList,
        system_prompt=system_prompt,
        model_settings=model_settings,
    )
    result = agent.run_sync(user_prompt)
    return result.output.themes


def search_web_for_themes(themes: list[str]) -> dict[str, list[ExaSearchResult]]:
    """Run an Exa search per theme; return a theme -> results mapping."""
    if get_exa_client() is None:
        logger.warning("Exa client unavailable; skipping web augmentation")
        return {theme: [] for theme in themes}

    results: dict[str, list[ExaSearchResult]] = {}
    for theme in themes:
        query = f"Recent developments and analysis of {theme}"
        try:
            results[theme] = exa_search(
                query,
                num_results=EXA_RESULTS_PER_THEME,
                max_characters=1200,
                category="news",
            )
        except Exception:
            logger.exception("Exa search failed for theme %r", theme)
            results[theme] = []
    return results


def _format_web_results(theme_results: dict[str, list[ExaSearchResult]]) -> str:
    if not theme_results:
        return "(no web results)"
    blocks: list[str] = []
    for theme, results in theme_results.items():
        blocks.append(f"Theme: {theme}")
        if not results:
            blocks.append("  (no results)")
            continue
        for result in results:
            snippet = (result.snippet or "").strip()
            if len(snippet) > 700:
                snippet = snippet[:700] + "…"
            published = f" ({result.published_date})" if result.published_date else ""
            blocks.append(f"  - {result.title}{published}")
            blocks.append(f"    URL: {result.url}")
            if snippet:
                blocks.append(f"    {snippet}")
    return "\n".join(blocks)


def synthesize_report(
    *,
    knowledge_items: list[KnowledgeItem],
    themes: list[str],
    theme_web_results: dict[str, list[ExaSearchResult]],
    model_spec: str = SYNTHESIS_MODEL,
    effort: str | None = None,
) -> InsightReport:
    """Run the final synthesis call that emits the structured report."""
    library_block = "\n\n".join(item.to_prompt_block() for item in knowledge_items)
    themes_block = "\n".join(f"- {t}" for t in themes) or "(no themes)"
    web_block = _format_web_results(theme_web_results)

    system_prompt = (
        "You are a sharp, senior editor writing a personal briefing for a single "
        "reader. You have two inputs: the reader's recent saved library (with "
        "summaries and key points) and fresh web results organized by theme. "
        "Your job is to synthesize, not repeat. Produce an insight report that "
        "ties items together, names tensions, and seeds follow-up conversations "
        "the reader might want to have with an AI assistant. Cite saved items "
        "by their [#content_id] when they meaningfully drive a point. Prefer "
        "confident, specific prose over hedging.\n\n"
        "For dig_deeper_areas, write 3-5 chat-starter prompts in the reader's "
        "own voice (first person). Each should pick up a specific thread from "
        "the report — a tension, an open question, a claim worth stress-testing "
        "— and phrase it as something the reader would type into a chat to keep "
        "exploring. Do NOT write search queries."
    )
    user_prompt = (
        "Use the reader's saved library and the fresh web findings to draft the "
        "insight report. Focus on non-obvious observations. End with 3-5 "
        "chat-starter prompts for dig_deeper_areas — first-person questions "
        "the reader can tap to continue the conversation.\n\n"
        f"Themes to organize the report around:\n{themes_block}\n\n"
        f"--- SAVED LIBRARY ---\n{library_block}\n\n"
        f"--- FRESH WEB FINDINGS ---\n{web_block}\n"
    )

    model, model_settings = build_pydantic_model(model_spec)
    effective_settings: Any = model_settings
    if effort:
        effective_settings = _apply_effort(model_settings, model_spec=model_spec, effort=effort)
    agent: Agent[None, InsightReport] = Agent(
        model,
        output_type=InsightReport,
        system_prompt=system_prompt,
        model_settings=effective_settings,
        output_retries=3,
    )
    result = agent.run_sync(user_prompt)
    return result.output


def _apply_effort(
    model_settings: Any,
    *,
    model_spec: str,
    effort: str,
) -> dict[str, Any]:
    """Inject a provider-appropriate reasoning-effort setting."""
    settings: dict[str, Any] = dict(model_settings) if model_settings else {}
    prefix = model_spec.split(":", 1)[0] if ":" in model_spec else ""
    if prefix == "anthropic" or model_spec.startswith("claude-"):
        settings["anthropic_effort"] = effort
    elif prefix == "openai" or model_spec.startswith(("openai:", "gpt-")):
        settings["openai_reasoning_effort"] = effort
    elif prefix in {"google-gla", "google"} or model_spec.startswith("gemini"):
        settings["google_thinking_config"] = {
            "include_thoughts": False,
            "thinking_level": effort,
        }
    else:
        logger.warning(
            "No effort mapping for model_spec=%s; ignoring effort=%s",
            model_spec,
            effort,
        )
    return settings


def generate_insight_report(
    db: Session,
    *,
    user_id: int,
    synthesis_model: str = SYNTHESIS_MODEL,
    effort: str | None = SYNTHESIS_EFFORT,
) -> InsightReport:
    """End-to-end: load knowledge, discover themes, search, synthesize."""
    knowledge_items = load_knowledge_items(db, user_id=user_id)
    if not knowledge_items:
        raise RuntimeError(f"No knowledge saves found for user_id={user_id}")

    logger.info("Loaded %d knowledge items for user_id=%s", len(knowledge_items), user_id)

    themes = extract_themes(knowledge_items)
    logger.info("Extracted themes: %s", themes)

    theme_web_results = search_web_for_themes(themes)
    total_web = sum(len(v) for v in theme_web_results.values())
    logger.info("Fetched %d web results across %d themes", total_web, len(themes))

    report = synthesize_report(
        knowledge_items=knowledge_items,
        themes=themes,
        theme_web_results=theme_web_results,
        model_spec=synthesis_model,
        effort=effort,
    )
    return report


def count_knowledge_saves_since(
    db: Session,
    *,
    user_id: int,
    since: datetime | None,
) -> int:
    """Count a user's knowledge saves created at-or-after ``since`` (UTC, naive).

    Passing ``since=None`` counts all of the user's saves. Used by the nightly
    trigger to decide whether enough new material has accumulated to warrant
    generating a fresh insight report.
    """
    stmt = select(func.count(ContentKnowledgeSave.id)).where(
        ContentKnowledgeSave.user_id == user_id,
    )
    if since is not None:
        stmt = stmt.where(ContentKnowledgeSave.saved_at >= since)
    return int(db.execute(stmt).scalar_one() or 0)


def last_insight_report_for_user(db: Session, *, user_id: int) -> tuple[int, datetime] | None:
    """Return ``(content_id, created_at)`` of the user's most recent insight report.

    Returns ``None`` when the user has no insight report yet.
    """
    stmt = (
        select(Content.id, Content.created_at)
        .where(Content.content_type == ContentType.INSIGHT_REPORT.value)
        .where(Content.content_metadata["user_id"].as_integer() == user_id)
        .order_by(Content.created_at.desc())
        .limit(1)
    )
    row = db.execute(stmt).first()
    if row is None:
        return None
    return int(row[0]), row[1]


def _build_insight_report_metadata(
    report: InsightReport,
    *,
    user_id: int,
    synthesis_model: str,
    effort: str | None,
    generated_at: datetime,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "source": "Newsly",
        "subtitle": report.subtitle,
        "intro": report.intro,
        "themes": list(report.themes),
        "insights": list(report.insights),
        "learnings": list(report.learnings),
        "curiosities": list(report.curiosities),
        "dig_deeper_areas": [
            {"title": area.title, "prompt": area.prompt} for area in report.dig_deeper_areas
        ],
        "referenced_knowledge_ids": list(report.referenced_knowledge_ids),
        "generated_at": generated_at.isoformat(),
        "generated_by_model": synthesis_model,
        "effort": effort,
    }


def _insight_report_url(*, user_id: int, generated_at: datetime) -> str:
    """Build a stable synthetic URL for the insight_report content row.

    The ``contents`` table enforces a unique ``(url, content_type)`` index, and
    downstream code (``content_to_domain``) validates the URL as an ``HttpUrl``.
    Use the internal ``https://newsly.app`` domain so the URL parses as valid
    http while never colliding with a real scraped article.
    """
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    return f"https://newsly.app/insight-report/user-{user_id}/{stamp}"


def persist_insight_report(
    db: Session,
    *,
    user_id: int,
    report: InsightReport,
    synthesis_model: str = SYNTHESIS_MODEL,
    effort: str | None = SYNTHESIS_EFFORT,
) -> Content:
    """Persist a generated InsightReport as a Content row + inbox entry.

    Creates:
    - One ``Content`` row with ``content_type = "insight_report"``, status
      ``completed``, classification ``to_read``, and metadata validated by
      :class:`InsightReportMetadata`.
    - One ``ContentStatusEntry`` with status ``inbox`` for the owning user so
      the feed-visibility join surfaces it (see ``content_feed_query``).

    Caller is responsible for committing the session. Returns the flushed
    ``Content`` row (``id`` populated).
    """
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    metadata = _build_insight_report_metadata(
        report,
        user_id=user_id,
        synthesis_model=synthesis_model,
        effort=effort,
        generated_at=now_utc,
    )
    content = Content(
        content_type=ContentType.INSIGHT_REPORT.value,
        url=_insight_report_url(user_id=user_id, generated_at=now_utc),
        title=report.title,
        source="Newsly",
        status=ContentStatus.COMPLETED.value,
        classification=ContentClassification.TO_READ.value,
        content_metadata=metadata,
        processed_at=now_utc,
        publication_date=now_utc,
    )
    db.add(content)
    db.flush()

    inbox_entry = ContentStatusEntry(
        user_id=user_id,
        content_id=content.id,
        status="inbox",
    )
    db.add(inbox_entry)
    db.flush()

    logger.info(
        "Persisted insight_report content_id=%s for user_id=%s",
        content.id,
        user_id,
    )
    return content
