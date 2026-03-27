"""Daily per-user news digest synthesis and enqueue helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, aliased

from app.constants import (
    ALLOWED_NEWS_DIGEST_INTERVAL_HOURS,
    CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY,
    CONTENT_STATUS_DIGEST_SOURCE,
    DAILY_NEWS_DIGEST_MODEL,
    DEFAULT_DAILY_DIGEST_SCHEDULER_LOOKBACK_HOURS,
    DEFAULT_NEWS_DIGEST_INTERVAL_HOURS,
)
from app.core.logging import get_logger
from app.models.metadata import ContentStatus as ContentLifecycleStatus
from app.models.metadata import DailyNewsRollupSummary
from app.models.schema import (
    Content,
    ContentDiscussion,
    ContentStatusEntry,
    DailyNewsDigest,
    ProcessingTask,
)
from app.services.llm_models import resolve_model
from app.services.llm_summarization import ContentSummarizer, get_content_summarizer
from app.services.queue import TaskStatus, TaskType, get_queue_service

logger = get_logger(__name__)

DAILY_DIGEST_GENERATION_HOUR = 3
MAX_POINTS_PER_SOURCE = 5
MAX_DAILY_DIGEST_BULLETS = 10
MIN_HIGH_VOLUME_DIGEST_BULLETS = 5
HIGH_VOLUME_DIGEST_SOURCE_COUNT = 10
ROLLUP_PROMPT_TOKEN_BUDGET = 900_000
ROLLUP_ESTIMATED_CHARS_PER_TOKEN = 4
STABLE_DAILY_NEWS_DIGEST_MODEL = "google-gla:gemini-flash-latest"
MAX_COMMENT_QUOTES_PER_SOURCE = 2
MIN_COMMENT_QUOTE_CHARS = 40
MAX_COMMENT_QUOTE_CHARS = 220


@dataclass(frozen=True)
class DailyDigestSourceItem:
    """Minimal source payload used to build one daily digest."""

    content_id: int
    title: str
    key_points: list[str]
    comment_quotes: list[str]


@dataclass(frozen=True)
class DailyDigestUpsertResult:
    """Result payload for daily digest generation."""

    digest_id: int | None
    local_date: date
    source_count: int
    created: bool
    skipped: bool = False


@dataclass(frozen=True)
class DailyDigestGenerationTarget:
    """Resolved checkpoint target for one digest generation run."""

    local_date: date
    coverage_end_at: datetime



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


def normalize_news_digest_interval_hours(interval_hours: int | None) -> int:
    """Validate and normalize a digest checkpoint interval."""
    if interval_hours is None:
        return DEFAULT_NEWS_DIGEST_INTERVAL_HOURS
    if interval_hours not in ALLOWED_NEWS_DIGEST_INTERVAL_HOURS:
        allowed_values = ", ".join(str(value) for value in ALLOWED_NEWS_DIGEST_INTERVAL_HOURS)
        raise ValueError(
            f"Invalid digest interval hours: {interval_hours}. Allowed: {allowed_values}"
        )
    return interval_hours


def _normalize_utc_datetime(value: datetime) -> datetime:
    """Convert aware or naive datetimes to naive UTC."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _checkpoint_hours_for_interval(interval_hours: int) -> tuple[int, ...]:
    """Return local checkpoint hours for a digest interval."""
    normalized_interval = normalize_news_digest_interval_hours(interval_hours)
    return tuple(range(0, 24, normalized_interval))



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


def _truncate_comment_quote(text: str, max_chars: int = MAX_COMMENT_QUOTE_CHARS) -> str:
    """Normalize and truncate one discussion quote."""
    normalized = " ".join(text.split()).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _format_comment_quote(text: str, author: str | None = None) -> str:
    """Format one comment snippet for roll-up prompt context."""
    formatted = f'"{text}"'
    clean_author = (author or "").strip()
    if clean_author and clean_author.lower() != "unknown":
        return f"{formatted} - {clean_author}"
    return formatted


def _extract_discussion_comment_quotes(discussion_data: dict[str, Any]) -> list[str]:
    """Extract a few high-signal discussion quotes for roll-up prompt context."""
    comments = discussion_data.get("comments")
    if not isinstance(comments, list):
        return []

    scored_quotes: list[tuple[int, int, str]] = []
    seen_texts: set[str] = set()

    for index, raw_comment in enumerate(comments):
        if not isinstance(raw_comment, dict):
            continue

        raw_text = raw_comment.get("text")
        if not isinstance(raw_text, str):
            continue

        normalized_text = " ".join(raw_text.split()).strip()
        if (
            len(normalized_text) < MIN_COMMENT_QUOTE_CHARS
            or "http://" in normalized_text
            or "https://" in normalized_text
        ):
            continue

        dedupe_key = normalized_text.casefold()
        if dedupe_key in seen_texts:
            continue
        seen_texts.add(dedupe_key)

        depth = raw_comment.get("depth")
        depth_value = depth if isinstance(depth, int) and depth >= 0 else 0
        score = 0
        score += max(0, 3 - min(depth_value, 3))
        score += min(len(normalized_text), 180) // 45
        if any(char.isdigit() for char in normalized_text):
            score += 1
        if "?" in normalized_text:
            score += 1
        if ":" in normalized_text or ";" in normalized_text:
            score += 1

        quote_text = _truncate_comment_quote(normalized_text)
        author = raw_comment.get("author")
        author_text = author if isinstance(author, str) else None
        scored_quotes.append((score, -index, _format_comment_quote(quote_text, author_text)))

    scored_quotes.sort(reverse=True)
    return [quote for _, _, quote in scored_quotes[:MAX_COMMENT_QUOTES_PER_SOURCE]]



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


def get_local_digest_window_utc_bounds(
    local_date: date,
    timezone_name: str,
    *,
    coverage_end_at: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Convert a local digest window into naive UTC bounds."""
    start_utc, end_utc = get_local_day_utc_bounds(local_date, timezone_name)
    if coverage_end_at is None:
        return start_utc, end_utc

    normalized_end = _normalize_utc_datetime(coverage_end_at)
    if normalized_end <= start_utc:
        return start_utc, start_utc
    if normalized_end >= end_utc:
        return start_utc, end_utc
    return start_utc, normalized_end


def resolve_daily_digest_generation_target(
    timezone_name: str,
    *,
    now_utc: datetime | None = None,
    interval_hours: int | None = None,
    lookback_hours: int = DEFAULT_DAILY_DIGEST_SCHEDULER_LOOKBACK_HOURS,
) -> DailyDigestGenerationTarget | None:
    """Return the latest checkpoint that should be generated for one user."""
    now_utc = now_utc or datetime.now(UTC)
    now_utc = now_utc.replace(tzinfo=UTC) if now_utc.tzinfo is None else now_utc.astimezone(UTC)

    timezone = ZoneInfo(normalize_timezone(timezone_name))
    local_now = now_utc.astimezone(timezone)
    local_window_start = local_now - timedelta(hours=lookback_hours)
    checkpoint_hours = _checkpoint_hours_for_interval(
        interval_hours or DEFAULT_NEWS_DIGEST_INTERVAL_HOURS
    )

    scheduled_candidates: list[tuple[datetime, date]] = []
    for candidate_date in (local_now.date(), (local_now - timedelta(days=1)).date()):
        for checkpoint_hour in checkpoint_hours:
            scheduled_local = datetime.combine(
                candidate_date,
                time(hour=checkpoint_hour),
                tzinfo=timezone,
            )
            if local_window_start < scheduled_local <= local_now:
                scheduled_candidates.append((scheduled_local, candidate_date))

    if not scheduled_candidates:
        return None

    scheduled_local, local_date = max(scheduled_candidates, key=lambda item: item[0])
    return DailyDigestGenerationTarget(
        local_date=local_date,
        coverage_end_at=scheduled_local.astimezone(UTC).replace(tzinfo=None),
    )



def resolve_target_local_date_for_generation(
    timezone_name: str,
    *,
    now_utc: datetime | None = None,
    generation_hour: int = DAILY_DIGEST_GENERATION_HOUR,
    window_hours: int = 1,
) -> date | None:
    """Backward-compatible wrapper for older date-only scheduling callers."""
    target = resolve_daily_digest_generation_target(
        timezone_name,
        now_utc=now_utc,
        interval_hours=(
            generation_hour if generation_hour in ALLOWED_NEWS_DIGEST_INTERVAL_HOURS else None
        ),
        lookback_hours=window_hours,
    )
    return target.local_date if target is not None else None



def collect_daily_news_sources(
    db: Session,
    *,
    user_id: int,
    local_date: date,
    timezone_name: str,
    coverage_end_at: datetime | None = None,
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
    start_utc, end_utc = get_local_digest_window_utc_bounds(
        local_date,
        timezone_name,
        coverage_end_at=coverage_end_at,
    )
    digest_status = aliased(ContentStatusEntry)
    digest_visibility = Content.content_metadata["digest_visibility"].as_string()
    query = (
        db.query(Content)
        .outerjoin(
            digest_status,
            and_(
                digest_status.content_id == Content.id,
                digest_status.user_id == user_id,
                digest_status.status == CONTENT_STATUS_DIGEST_SOURCE,
            ),
        )
        .filter(Content.content_type == "news")
        .filter(Content.status == ContentLifecycleStatus.COMPLETED.value)
        .filter((Content.classification != "skip") | (Content.classification.is_(None)))
        .filter(Content.created_at >= start_utc, Content.created_at < end_utc)
        .filter(
            or_(
                digest_visibility.is_(None),
                digest_visibility != CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY,
                digest_status.id.is_not(None),
            )
        )
        .order_by(Content.created_at.desc(), Content.id.desc())
    )
    if max_items is not None:
        query = query.limit(max_items)

    rows = query.all()
    content_ids = [row.id for row in rows if getattr(row, "id", None) is not None]
    discussions_by_content_id: dict[int, ContentDiscussion] = {}
    if content_ids:
        discussion_rows = (
            db.query(ContentDiscussion)
            .filter(ContentDiscussion.content_id.in_(content_ids))
            .all()
        )
        discussions_by_content_id = {
            row.content_id: row for row in discussion_rows if isinstance(row.content_id, int)
        }

    sources: list[DailyDigestSourceItem] = []
    for content in rows:
        metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
        title = _extract_source_title(content, metadata)
        points = _extract_summary_points(metadata)
        discussion = discussions_by_content_id.get(content.id)
        discussion_data = (
            discussion.discussion_data
            if discussion is not None and isinstance(discussion.discussion_data, dict)
            else {}
        )
        comment_quotes = _extract_discussion_comment_quotes(discussion_data)
        sources.append(
            DailyDigestSourceItem(
                content_id=content.id,
                title=title,
                key_points=points,
                comment_quotes=comment_quotes,
            )
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
    if source.comment_quotes:
        lines.append("Comment quotes:")
        lines.extend(f"- {quote}" for quote in source.comment_quotes)
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
            "Synthesize a daily news rollup from the following source stories and comment quotes:",
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
        "Synthesize a daily news rollup from the following source stories and comment quotes:",
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
    model_spec: str = DAILY_NEWS_DIGEST_MODEL,
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
    _, resolved_model = resolve_model(None, model_spec)
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


def _clean_rollup_key_points(raw_points: list[str]) -> list[str]:
    """Normalize stored digest key points."""
    cleaned_points = [
        point.strip()
        for point in raw_points
        if isinstance(point, str) and point.strip()
    ]
    return cleaned_points[:MAX_DAILY_DIGEST_BULLETS]


def _validate_rollup_summary(
    summary: DailyNewsRollupSummary,
    *,
    source_count: int,
) -> tuple[str, str, list[str]]:
    """Validate a daily rollup before persisting it."""
    title = (summary.title or "").strip()
    if not title:
        raise ValueError("Daily digest title was empty")

    summary_text = (summary.summary or "").strip()
    if not summary_text:
        raise ValueError("Daily digest summary was empty")

    key_points = _clean_rollup_key_points(summary.key_points)
    if (
        source_count >= HIGH_VOLUME_DIGEST_SOURCE_COUNT
        and len(key_points) < MIN_HIGH_VOLUME_DIGEST_BULLETS
    ):
        raise ValueError(
            "Daily digest produced too few key points for a high-volume day"
        )

    return title, summary_text, key_points


def digest_requires_regeneration(
    digest: DailyNewsDigest,
    *,
    min_high_volume_bullets: int = MIN_HIGH_VOLUME_DIGEST_BULLETS,
    high_volume_source_count: int = HIGH_VOLUME_DIGEST_SOURCE_COUNT,
) -> bool:
    """Return True when a stored digest is too sparse to keep."""
    title = (digest.title or "").strip()
    summary_text = (digest.summary or "").strip()
    key_points = _clean_rollup_key_points(digest.key_points or [])

    if not title or not summary_text:
        return True
    return (
        int(digest.source_count or 0) >= high_volume_source_count
        and len(key_points) < min_high_volume_bullets
    )


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


def _digest_has_same_sources(
    digest: DailyNewsDigest,
    source_content_ids: list[int],
) -> bool:
    """Return True when the stored digest already reflects the same sources."""
    existing_source_ids = (
        [int(content_id) for content_id in digest.source_content_ids]
        if isinstance(digest.source_content_ids, list)
        else []
    )
    return existing_source_ids == source_content_ids


def _digest_payload_changed(
    digest: DailyNewsDigest,
    *,
    title: str,
    summary_text: str,
    key_points: list[str],
    source_content_ids: list[int],
    source_count: int,
) -> bool:
    """Return True when persisted digest content differs from new synthesis."""
    existing_key_points = (
        [point for point in digest.key_points if isinstance(point, str)]
        if isinstance(digest.key_points, list)
        else []
    )
    existing_source_ids = (
        [int(content_id) for content_id in digest.source_content_ids]
        if isinstance(digest.source_content_ids, list)
        else []
    )
    return any(
        [
            digest.title != title,
            digest.summary != summary_text,
            existing_key_points != key_points,
            existing_source_ids != source_content_ids,
            int(digest.source_count or 0) != source_count,
        ]
    )



def upsert_daily_news_digest_for_user_day(
    db: Session,
    *,
    user_id: int,
    local_date: date,
    timezone_name: str,
    summarizer: ContentSummarizer | None = None,
    force_regenerate: bool = False,
    coverage_end_at: datetime | None = None,
    skip_if_empty: bool = False,
) -> DailyDigestUpsertResult:
    """Generate or update one per-user daily digest row.

    Args:
        db: Database session.
        user_id: User ID.
        local_date: User-local day to summarize.
        timezone_name: User timezone.
        summarizer: Optional shared summarizer for test injection.
        force_regenerate: Recompute even when digest row already exists.
        coverage_end_at: Optional UTC checkpoint end time for same-day digest updates.
        skip_if_empty: Skip persistence entirely when no stories exist yet.

    Returns:
        Upsert result with digest identifier.
    """
    timezone_name = normalize_timezone(timezone_name)
    target_coverage_end_at = _normalize_utc_datetime(coverage_end_at) if coverage_end_at else None
    if target_coverage_end_at is None:
        _, target_coverage_end_at = get_local_day_utc_bounds(local_date, timezone_name)
    existing = (
        db.query(DailyNewsDigest)
        .filter(DailyNewsDigest.user_id == user_id, DailyNewsDigest.local_date == local_date)
        .first()
    )

    if (
        existing
        and not force_regenerate
        and existing.coverage_end_at is not None
        and existing.coverage_end_at >= target_coverage_end_at
    ):
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
        coverage_end_at=target_coverage_end_at,
    )
    source_content_ids = [source.content_id for source in sources]

    if skip_if_empty and not sources and existing is None:
        return DailyDigestUpsertResult(
            digest_id=None,
            local_date=local_date,
            source_count=0,
            created=False,
            skipped=True,
        )

    if (
        existing is not None
        and not force_regenerate
        and _digest_has_same_sources(existing, source_content_ids)
    ):
        existing.timezone = timezone_name
        existing.generated_at = datetime.now(UTC).replace(tzinfo=None)
        existing.coverage_end_at = target_coverage_end_at
        db.commit()
        db.refresh(existing)
        return DailyDigestUpsertResult(
            digest_id=existing.id,
            local_date=local_date,
            source_count=len(sources),
            created=False,
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

    if sources:
        try:
            title, summary_text, key_points = _validate_rollup_summary(
                summary,
                source_count=len(sources),
            )
        except ValueError as primary_error:
            if resolved_model == STABLE_DAILY_NEWS_DIGEST_MODEL:
                raise
            logger.warning(
                "Daily digest validation failed for user %s on %s with model %s; retrying "
                "with stable model %s",
                user_id,
                local_date.isoformat(),
                resolved_model,
                STABLE_DAILY_NEWS_DIGEST_MODEL,
                extra={
                    "component": "daily_news_digest",
                    "operation": "validate_rollup",
                    "context_data": {
                        "user_id": user_id,
                        "local_date": local_date.isoformat(),
                        "source_count": len(sources),
                        "resolved_model": resolved_model,
                        "error": str(primary_error),
                    },
                },
            )
            summary, resolved_model = _synthesize_daily_rollup(
                summarizer=effective_summarizer,
                local_date=local_date,
                sources=sources,
                model_spec=STABLE_DAILY_NEWS_DIGEST_MODEL,
            )
            title, summary_text, key_points = _validate_rollup_summary(
                summary,
                source_count=len(sources),
            )
    else:
        key_points = []
        summary_text = _daily_digest_summary_text(
            key_points=key_points,
            fallback_text=summary.summary,
        )
        title = _daily_digest_title(local_date, summary.title)

    digest = existing or DailyNewsDigest(user_id=user_id, local_date=local_date)
    content_changed = existing is None or _digest_payload_changed(
        digest,
        title=title[:240],
        summary_text=summary_text,
        key_points=key_points,
        source_content_ids=source_content_ids,
        source_count=len(sources),
    )
    digest.timezone = timezone_name
    digest.title = title[:240]
    digest.summary = summary_text
    digest.key_points = key_points
    digest.source_content_ids = source_content_ids
    digest.source_count = len(sources)
    digest.llm_model = resolved_model
    digest.generated_at = datetime.now(UTC).replace(tzinfo=None)
    digest.coverage_end_at = target_coverage_end_at
    if existing is not None and content_changed:
        digest.read_at = None

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


def _pending_digest_task_exists(
    db: Session,
    *,
    user_id: int,
    local_date: date,
    coverage_end_at: datetime | None = None,
) -> int | None:
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
    target_coverage_end_at = (
        _normalize_utc_datetime(coverage_end_at).isoformat()
        if coverage_end_at is not None
        else None
    )
    for task in candidate_tasks:
        payload = task.payload if isinstance(task.payload, dict) else {}
        if (
            payload.get("user_id") == user_id
            and payload.get("local_date") == target_date
            and payload.get("coverage_end_at") == target_coverage_end_at
        ):
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
    coverage_end_at: datetime | None = None,
    skip_if_empty: bool = False,
) -> int | None:
    """Enqueue one daily digest task when no matching task is already pending.

    Args:
        db: Database session.
        user_id: User ID.
        local_date: Target digest local date.
        timezone_name: User timezone.
        trigger: Trigger label for observability.
        force_regenerate: When ``True``, enqueue even when a digest row already exists.
        coverage_end_at: Optional UTC checkpoint end time for same-day digests.
        skip_if_empty: Skip persistence when the checkpoint window has no stories.

    Returns:
        Enqueued task id, existing task id, or ``None`` when digest already exists.
    """
    normalized_coverage_end_at = (
        _normalize_utc_datetime(coverage_end_at) if coverage_end_at is not None else None
    )

    if not force_regenerate:
        existing_digest = (
            db.query(DailyNewsDigest)
            .filter(DailyNewsDigest.user_id == user_id, DailyNewsDigest.local_date == local_date)
            .first()
        )
        if existing_digest is not None and normalized_coverage_end_at is None:
            return None
        if (
            existing_digest is not None
            and normalized_coverage_end_at is not None
            and existing_digest.coverage_end_at is not None
            and existing_digest.coverage_end_at >= normalized_coverage_end_at
        ):
            return None

    existing_task_id = _pending_digest_task_exists(
        db,
        user_id=user_id,
        local_date=local_date,
        coverage_end_at=normalized_coverage_end_at,
    )
    if existing_task_id is not None:
        return existing_task_id

    queue_service = get_queue_service()
    task_id = queue_service.enqueue(
        TaskType.GENERATE_DAILY_NEWS_DIGEST,
        payload={
            "user_id": user_id,
            "local_date": local_date.isoformat(),
            "timezone": normalize_timezone(timezone_name),
            "coverage_end_at": (
                normalized_coverage_end_at.isoformat()
                if normalized_coverage_end_at is not None
                else None
            ),
            "trigger": trigger,
            "force_regenerate": force_regenerate,
            "skip_if_empty": skip_if_empty,
        },
        dedupe=False,
    )
    return task_id
