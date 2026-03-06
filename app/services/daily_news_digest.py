"""Daily per-user news digest synthesis and enqueue helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.constants import DAILY_NEWS_DIGEST_MODEL
from app.core.logging import get_logger
from app.models.metadata import DailyNewsRollupSummary
from app.models.schema import Content, DailyNewsDigest, ProcessingTask
from app.repositories.content_feed_query import build_user_feed_query
from app.services.llm_models import resolve_model
from app.services.llm_summarization import ContentSummarizer, get_content_summarizer
from app.services.queue import TaskStatus, TaskType, get_queue_service

logger = get_logger(__name__)

DAILY_DIGEST_GENERATION_HOUR = 3
MAX_POINTS_PER_SOURCE = 5
MAX_DAILY_DIGEST_BULLETS = 10
ROLLUP_PROMPT_TOKEN_BUDGET = 900_000
ROLLUP_ESTIMATED_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class DailyDigestSourceItem:
    """Minimal source payload used to build one daily digest."""

    content_id: int
    title: str
    key_points: list[str]


@dataclass(frozen=True)
class DailyDigestUpsertResult:
    """Result payload for daily digest generation."""

    digest_id: int
    local_date: date
    source_count: int
    created: bool



def normalize_timezone(timezone_name: str | None) -> str:
    """Validate and normalize a timezone value.

    Args:
        timezone_name: Raw timezone string.

    Returns:
        Valid IANA timezone name. Defaults to ``UTC``.
    """
    if timezone_name is None:
        return "UTC"

    candidate = timezone_name.strip() or "UTC"
    try:
        ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        logger.warning("Invalid timezone '%s'; falling back to UTC", candidate)
        return "UTC"
    return candidate



def _extract_summary_points(metadata: dict[str, Any]) -> list[str]:
    """Extract summary points from news metadata.

    Args:
        metadata: Content metadata payload.

    Returns:
        Ordered list of cleaned points.
    """
    summary = metadata.get("summary")
    points: list[str] = []

    if isinstance(summary, dict):
        raw_points = summary.get("key_points")
        if isinstance(raw_points, list):
            for raw in raw_points:
                if not isinstance(raw, str):
                    continue
                point = raw.strip()
                if point:
                    points.append(point)

        if not points:
            for key in ("summary", "overview", "hook", "takeaway"):
                candidate = summary.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    points.append(candidate.strip())
                    break

    return points[:MAX_POINTS_PER_SOURCE]



def _extract_source_title(content: Content, metadata: dict[str, Any]) -> str:
    """Extract best-effort title for digest source input.

    Args:
        content: Content record.
        metadata: Content metadata payload.

    Returns:
        Clean title string.
    """
    if content.title and content.title.strip():
        return content.title.strip()

    article = metadata.get("article")
    if isinstance(article, dict):
        title = article.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()

    return f"News item #{content.id}"



def get_local_day_utc_bounds(local_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    """Convert local-day boundaries to naive UTC datetime bounds.

    Args:
        local_date: User-local day.
        timezone_name: User timezone.

    Returns:
        Tuple of ``(start_utc_naive, end_utc_naive)``.
    """
    timezone = ZoneInfo(normalize_timezone(timezone_name))
    local_start = datetime.combine(local_date, time.min, tzinfo=timezone)
    local_end = local_start + timedelta(days=1)
    start_utc = local_start.astimezone(UTC).replace(tzinfo=None)
    end_utc = local_end.astimezone(UTC).replace(tzinfo=None)
    return start_utc, end_utc



def resolve_target_local_date_for_generation(
    timezone_name: str,
    *,
    now_utc: datetime | None = None,
    generation_hour: int = DAILY_DIGEST_GENERATION_HOUR,
) -> date | None:
    """Return the digest local date to generate for the current local hour.

    Args:
        timezone_name: User timezone.
        now_utc: Current UTC timestamp (for tests).
        generation_hour: Hour-of-day in local time to generate digests.

    Returns:
        ``local_today - 1`` when local hour matches generation hour; otherwise ``None``.
    """
    now_utc = now_utc or datetime.now(UTC)
    timezone = ZoneInfo(normalize_timezone(timezone_name))
    local_now = now_utc.astimezone(timezone)
    if local_now.hour != generation_hour:
        return None
    return local_now.date() - timedelta(days=1)



def collect_daily_news_sources(
    db: Session,
    *,
    user_id: int,
    local_date: date,
    timezone_name: str,
    max_items: int | None = None,
) -> list[DailyDigestSourceItem]:
    """Collect digest source rows for one user and local day.

    Args:
        db: Database session.
        user_id: User ID.
        local_date: User-local day.
        timezone_name: User timezone.
        max_items: Optional limit for testing or backfill safety.

    Returns:
        List of digest source items.
    """
    start_utc, end_utc = get_local_day_utc_bounds(local_date, timezone_name)

    query = (
        build_user_feed_query(db, user_id, mode="inbox")
        .filter(Content.content_type == "news")
        .filter(Content.created_at >= start_utc, Content.created_at < end_utc)
        .order_by(Content.created_at.desc(), Content.id.desc())
    )
    if max_items is not None:
        query = query.limit(max_items)

    rows = query.all()

    sources: list[DailyDigestSourceItem] = []
    for row in rows:
        content = row[0]
        metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
        title = _extract_source_title(content, metadata)
        points = _extract_summary_points(metadata)
        sources.append(
            DailyDigestSourceItem(content_id=content.id, title=title, key_points=points)
        )

    return sources


def _estimate_tokens(text: str) -> int:
    """Estimate prompt tokens with a conservative chars-per-token heuristic."""
    return max(
        1,
        (len(text) + ROLLUP_ESTIMATED_CHARS_PER_TOKEN - 1) // ROLLUP_ESTIMATED_CHARS_PER_TOKEN,
    )


def _build_rollup_source_block(index: int, source: DailyDigestSourceItem) -> str:
    """Render one source story into a compact prompt block."""
    lines = [
        f"Story {index}:",
        f"Title: {source.title}",
    ]
    if source.key_points:
        lines.append("Signals:")
        lines.extend(f"- {point}" for point in source.key_points)
    else:
        lines.append("Signals: (none)")
    return "\n".join(lines)


def _select_rollup_prompt_sources(
    *,
    local_date: date,
    sources: list[DailyDigestSourceItem],
    token_budget: int = ROLLUP_PROMPT_TOKEN_BUDGET,
) -> list[DailyDigestSourceItem]:
    """Select as many newest-first sources as fit within the prompt token budget."""
    selected: list[DailyDigestSourceItem] = []
    static_text = "\n".join(
        [
            f"Digest date: {local_date.isoformat()}",
            "",
            "Synthesize a daily news rollup from the following source stories:",
        ]
    )
    used_tokens = _estimate_tokens(static_text)

    for source in sources:
        next_index = len(selected) + 1
        source_tokens = _estimate_tokens(_build_rollup_source_block(next_index, source))
        if selected and used_tokens + source_tokens > token_budget:
            break
        if not selected and source_tokens >= token_budget:
            return [source]
        if used_tokens + source_tokens > token_budget:
            break
        selected.append(source)
        used_tokens += source_tokens

    return selected or sources[:1]


def _build_rollup_prompt_input(local_date: date, sources: list[DailyDigestSourceItem]) -> str:
    """Create LLM input payload for daily roll-up synthesis.

    Args:
        local_date: Digest date.
        sources: Source items for that day.

    Returns:
        Prompt input text containing only title + key points per item.
    """
    lines: list[str] = [
        f"Digest date: {local_date.isoformat()}",
        "",
        "Synthesize a daily news rollup from the following source stories:",
    ]

    if not sources:
        lines.extend(
            [
                "",
                "No news sources were available for this day.",
                "Return a concise title and summary indicating no major updates.",
            ]
        )
        return "\n".join(lines)

    for index, source in enumerate(sources, start=1):
        lines.append("")
        lines.append(_build_rollup_source_block(index, source))

    return "\n".join(lines)



def _synthesize_daily_rollup(
    *,
    summarizer: ContentSummarizer,
    local_date: date,
    sources: list[DailyDigestSourceItem],
) -> tuple[DailyNewsRollupSummary, str]:
    """Generate a daily news digest with Google Flash.

    Args:
        summarizer: Shared summarizer service.
        local_date: Digest local date.
        sources: Source rows.

    Returns:
        Tuple ``(rollup_summary, resolved_model_spec)``.

    Raises:
        ValueError: When the summarizer returns an unexpected output.
    """
    prompt_sources = _select_rollup_prompt_sources(local_date=local_date, sources=sources)
    prompt_input = _build_rollup_prompt_input(local_date, prompt_sources)
    _, resolved_model = resolve_model(None, DAILY_NEWS_DIGEST_MODEL)
    model_hint = resolved_model.split(":", 1)[1] if ":" in resolved_model else resolved_model
    summary = summarizer.summarize_content(
        prompt_input,
        content_type="daily_news_rollup",
        max_quotes=0,
        provider_override="google",
        model_hint=model_hint,
    )
    logger.info(
        "Daily digest rollup prompt built",
        extra={
            "component": "daily_news_digest",
            "operation": "synthesize_rollup",
            "context_data": {
                "local_date": local_date.isoformat(),
                "total_source_count": len(sources),
                "prompt_source_count": len(prompt_sources),
                "estimated_prompt_tokens": _estimate_tokens(prompt_input),
                "model": resolved_model,
            },
        },
    )
    if not isinstance(summary, DailyNewsRollupSummary):
        raise ValueError("Daily digest synthesis returned unexpected summary payload")
    return summary, resolved_model


def _daily_digest_title(local_date: date, generated_title: str | None = None) -> str:
    """Return the stored title for a daily digest."""
    title = (generated_title or "").strip()
    if title:
        return title
    return local_date.isoformat()


def _daily_digest_summary_text(*, key_points: list[str], fallback_text: str | None = None) -> str:
    """Return the stored summary text for a digest.

    Preserve the model overview when available and only synthesize fallback
    copy when both the overview and bullets are empty.
    """
    summary_text = (fallback_text or "").strip()
    if summary_text:
        return summary_text
    if key_points:
        return ""
    return "No major completed news stories were available for this day."


def _fallback_rollup(local_date: date) -> DailyNewsRollupSummary:
    """Create fallback summary payload when no source data exists.

    Args:
        local_date: Digest local date.

    Returns:
        DailyNewsRollupSummary payload.
    """
    return DailyNewsRollupSummary(
        title=_daily_digest_title(local_date),
        key_points=[],
        summary="No major completed news stories were available for this day.",
    )



def upsert_daily_news_digest_for_user_day(
    db: Session,
    *,
    user_id: int,
    local_date: date,
    timezone_name: str,
    summarizer: ContentSummarizer | None = None,
    force_regenerate: bool = False,
) -> DailyDigestUpsertResult:
    """Generate or update one per-user daily digest row.

    Args:
        db: Database session.
        user_id: User ID.
        local_date: User-local day to summarize.
        timezone_name: User timezone.
        summarizer: Optional shared summarizer for test injection.
        force_regenerate: Recompute even when digest row already exists.

    Returns:
        Upsert result with digest identifier.
    """
    timezone_name = normalize_timezone(timezone_name)
    existing = (
        db.query(DailyNewsDigest)
        .filter(DailyNewsDigest.user_id == user_id, DailyNewsDigest.local_date == local_date)
        .first()
    )

    if existing and not force_regenerate:
        return DailyDigestUpsertResult(
            digest_id=existing.id,
            local_date=local_date,
            source_count=int(existing.source_count or 0),
            created=False,
        )

    sources = collect_daily_news_sources(
        db,
        user_id=user_id,
        local_date=local_date,
        timezone_name=timezone_name,
    )

    effective_summarizer = summarizer or get_content_summarizer()
    if sources:
        summary, resolved_model = _synthesize_daily_rollup(
            summarizer=effective_summarizer,
            local_date=local_date,
            sources=sources,
        )
    else:
        summary = _fallback_rollup(local_date)
        resolved_model = DAILY_NEWS_DIGEST_MODEL

    key_points = [
        point.strip()
        for point in summary.key_points
        if isinstance(point, str) and point.strip()
    ]
    summary_text = _daily_digest_summary_text(
        key_points=key_points,
        fallback_text=summary.summary,
    )
    title = _daily_digest_title(local_date, summary.title)

    digest = existing or DailyNewsDigest(user_id=user_id, local_date=local_date)
    digest.timezone = timezone_name
    digest.title = title[:240]
    digest.summary = summary_text
    digest.key_points = key_points
    digest.source_content_ids = [source.content_id for source in sources]
    digest.source_count = len(sources)
    digest.llm_model = resolved_model
    digest.generated_at = datetime.now(UTC).replace(tzinfo=None)

    if existing is None:
        db.add(digest)

    db.commit()
    db.refresh(digest)

    return DailyDigestUpsertResult(
        digest_id=digest.id,
        local_date=local_date,
        source_count=len(sources),
        created=existing is None,
    )



def _pending_digest_task_exists(db: Session, *, user_id: int, local_date: date) -> int | None:
    """Return existing pending/processing task id when one already matches payload.

    Args:
        db: Database session.
        user_id: User ID.
        local_date: Target local date.

    Returns:
        Existing task ID when found, otherwise ``None``.
    """
    candidate_tasks = (
        db.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.GENERATE_DAILY_NEWS_DIGEST.value)
        .filter(
            ProcessingTask.status.in_([TaskStatus.PENDING.value, TaskStatus.PROCESSING.value])
        )
        .order_by(ProcessingTask.id.desc())
        .limit(200)
        .all()
    )

    target_date = local_date.isoformat()
    for task in candidate_tasks:
        payload = task.payload if isinstance(task.payload, dict) else {}
        if payload.get("user_id") == user_id and payload.get("local_date") == target_date:
            return task.id

    return None



def enqueue_daily_news_digest_task(
    db: Session,
    *,
    user_id: int,
    local_date: date,
    timezone_name: str,
    trigger: str = "cron",
    force_regenerate: bool = False,
) -> int | None:
    """Enqueue one daily digest task when no matching task is already pending.

    Args:
        db: Database session.
        user_id: User ID.
        local_date: Target digest local date.
        timezone_name: User timezone.
        trigger: Trigger label for observability.
        force_regenerate: When ``True``, enqueue even when a digest row already exists.

    Returns:
        Enqueued task id, existing task id, or ``None`` when digest already exists.
    """
    if not force_regenerate:
        existing_digest = (
            db.query(DailyNewsDigest)
            .filter(DailyNewsDigest.user_id == user_id, DailyNewsDigest.local_date == local_date)
            .first()
        )
        if existing_digest is not None:
            return None

    existing_task_id = _pending_digest_task_exists(db, user_id=user_id, local_date=local_date)
    if existing_task_id is not None:
        return existing_task_id

    queue_service = get_queue_service()
    task_id = queue_service.enqueue(
        TaskType.GENERATE_DAILY_NEWS_DIGEST,
        payload={
            "user_id": user_id,
            "local_date": local_date.isoformat(),
            "timezone": normalize_timezone(timezone_name),
            "trigger": trigger,
            "force_regenerate": force_regenerate,
        },
        dedupe=False,
    )
    return task_id
