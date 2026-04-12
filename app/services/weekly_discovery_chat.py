"""Weekly discovery chat session creation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.schema import (
    ChatSession,
    Content,
    ContentReadStatus,
    FeedDiscoveryRun,
    FeedDiscoverySuggestion,
    OnboardingDiscoveryRun,
)
from app.models.user import User
from app.services.assistant_router import seed_assistant_message
from app.services.llm_models import DEFAULT_MODEL, DEFAULT_PROVIDER
from app.utils.title_utils import resolve_content_display_title

logger = get_logger(__name__)


def _require_session_id(session: ChatSession) -> int:
    """Return a persisted session ID or raise."""
    session_id = session.id
    if session_id is None:
        raise ValueError("Chat session must be persisted before use")
    return session_id


@dataclass
class WeeklyDiscoverySeed:
    """Seed material for a weekly discovery session."""

    local_date: str
    week_key: str
    week_label: str
    topic_summary: str | None
    inferred_topics: list[str]
    recent_reads: list[tuple[int, str, str]]
    suggestions: list[FeedDiscoverySuggestion]


def _user_local_date(user: User, reference_time: datetime | None = None) -> date:
    tz_name = "UTC"
    tz = ZoneInfo(tz_name)
    now = reference_time or datetime.now(UTC)
    return now.astimezone(tz).date()


def _sunday_week_start(local_date: date) -> date:
    days_since_sunday = (local_date.weekday() + 1) % 7
    return local_date - timedelta(days=days_since_sunday)


def _build_seed(db: Session, user: User) -> WeeklyDiscoverySeed:
    local_date_value = _user_local_date(user)
    week_start = _sunday_week_start(local_date_value)
    local_date = local_date_value.isoformat()
    recent_rows = (
        db.query(Content)
        .join(ContentReadStatus, ContentReadStatus.content_id == Content.id)
        .filter(ContentReadStatus.user_id == user.id)
        .order_by(ContentReadStatus.read_at.desc())
        .limit(6)
        .all()
    )
    recent_reads = [
        (
            row.id,
            resolve_content_display_title(
                title=row.title,
                metadata=row.content_metadata,
                fallback="Untitled",
            ),
            row.url,
        )
        for row in recent_rows
        if row.id is not None and row.url
    ]

    onboarding_run = (
        db.query(OnboardingDiscoveryRun)
        .filter(OnboardingDiscoveryRun.user_id == user.id)
        .order_by(OnboardingDiscoveryRun.created_at.desc())
        .first()
    )
    topic_summary = onboarding_run.topic_summary if onboarding_run else None
    inferred_topics = list(onboarding_run.inferred_topics or []) if onboarding_run else []

    latest_run = (
        db.query(FeedDiscoveryRun)
        .filter(FeedDiscoveryRun.user_id == user.id, FeedDiscoveryRun.status == "completed")
        .order_by(FeedDiscoveryRun.created_at.desc())
        .first()
    )
    suggestions: list[FeedDiscoverySuggestion] = []
    if latest_run is not None:
        suggestions = (
            db.query(FeedDiscoverySuggestion)
            .filter(
                FeedDiscoverySuggestion.user_id == user.id,
                FeedDiscoverySuggestion.run_id == latest_run.id,
                FeedDiscoverySuggestion.status == "new",
            )
            .order_by(FeedDiscoverySuggestion.score.desc().nullslast())
            .limit(5)
            .all()
        )

    return WeeklyDiscoverySeed(
        local_date=local_date,
        week_key=f"weekly:{week_start.isoformat()}",
        week_label=week_start.isoformat(),
        topic_summary=topic_summary,
        inferred_topics=inferred_topics,
        recent_reads=recent_reads,
        suggestions=suggestions,
    )


def _build_context_snapshot(seed: WeeklyDiscoverySeed) -> str:
    lines = [
        f"Weekly discovery date: {seed.local_date}",
        f"Weekly discovery week: {seed.week_label}",
    ]
    if seed.topic_summary:
        lines.append(f"Onboarding summary: {seed.topic_summary}")
    if seed.inferred_topics:
        lines.append(f"Inferred topics: {', '.join(seed.inferred_topics[:8])}")
    if seed.recent_reads:
        lines.append("Recent reads:")
        for content_id, title, url in seed.recent_reads:
            lines.append(f"- [{content_id}] {title} — {url}")
    if seed.suggestions:
        lines.append("Fresh discovery suggestions:")
        for suggestion in seed.suggestions:
            title = (
                suggestion.title or suggestion.feed_url or suggestion.site_url or "Untitled"
            ).strip()
            rationale = (suggestion.rationale or "").strip()
            lines.append(f"- {title}")
            if rationale:
                lines.append(f"  {rationale}")
    return "\n".join(lines)


def _build_seed_message(seed: WeeklyDiscoverySeed) -> str:
    intro = f"Here are a few things worth exploring for the week of {seed.week_label}."
    if seed.suggestions:
        lines = [intro, "", "Fresh suggestions:"]
        for suggestion in seed.suggestions:
            title = (
                suggestion.title or suggestion.feed_url or suggestion.site_url or "Untitled"
            ).strip()
            rationale = (suggestion.rationale or "").strip()
            lines.append(f"- {title}")
            if rationale:
                lines.append(f"  Why it stands out: {rationale}")
        lines.append("")
        lines.append(
            "Reply with things like “add the first two to my feed”, "
            "“subscribe me to the podcast”, or “find more like this”."
        )
        return "\n".join(lines)

    if seed.recent_reads:
        titles = ", ".join(title for _, title, _ in seed.recent_reads[:3])
        return (
            f"{intro}\n\n"
            f"I don't have fresh discovery suggestions yet, but your recent reading has clustered "
            f"around: {titles}. Ask me to find related articles, podcasts, or feeds."
        )

    if seed.inferred_topics:
        return (
            f"{intro}\n\n"
            f"I'll use your onboarding interests as the starting point: "
            f"{', '.join(seed.inferred_topics[:5])}. Ask me to find something new."
        )

    return (
        f"{intro}\n\n"
        "I don't have enough personalized signal yet. Ask me for a topic and I'll start "
        "building your weekly discovery thread from there."
    )


def ensure_weekly_discovery_session(
    db: Session,
    *,
    user_id: int,
) -> ChatSession | None:
    """Create one fresh weekly discovery chat session for the current local week."""
    user = (
        db.query(User).filter(User.id == user_id, User.has_completed_onboarding.is_(True)).first()
    )
    if user is None:
        return None

    seed = _build_seed(db, user)
    existing = (
        db.query(ChatSession)
        .filter(
            ChatSession.user_id == user_id,
            ChatSession.session_type == "weekly_discovery",
            ChatSession.topic == seed.week_key,
            ChatSession.is_archived.is_(False),
        )
        .first()
    )
    if existing is not None:
        return existing

    session = ChatSession(
        user_id=user_id,
        content_id=None,
        title=f"Weekly Discovery • {seed.week_label}",
        session_type="weekly_discovery",
        topic=seed.week_key,
        context_snapshot=_build_context_snapshot(seed),
        llm_provider=DEFAULT_PROVIDER,
        llm_model=DEFAULT_MODEL,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        last_message_at=datetime.now(UTC),
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    seed_assistant_message(
        db,
        session_id=_require_session_id(session),
        assistant_text=_build_seed_message(seed),
    )
    session.last_message_at = datetime.now(UTC)
    db.commit()
    db.refresh(session)
    logger.info(
        "Created weekly discovery session",
        extra={
            "component": "weekly_discovery_chat",
            "operation": "create_session",
            "item_id": str(user_id),
            "context_data": {"session_id": session.id, "local_date": seed.local_date},
        },
    )
    return session
