"""Weekly discovery chat session creation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.contracts import FeedFormat, FeedType, MessageProcessingStatus
from app.models.db import (
    ChatMessage,
    ChatSession,
    Content,
    ContentReadStatus,
    FeedDiscoveryRun,
    FeedDiscoverySuggestion,
    OnboardingDiscoveryRun,
)
from app.models.db.users import User
from app.models.domain.chat_render import (
    AssistantFeedOption,
    ChatMessageRenderMetadata,
    build_assistant_feed_option_id,
)
from app.models.internal.scraper_configs import (
    canonicalize_feed_url,
    normalize_feed_type_alias,
)
from app.services.assistant_router import seed_assistant_message
from app.services.feed_subscription import load_active_feed_urls
from app.services.llm_models import DEFAULT_MODEL, DEFAULT_PROVIDER
from app.utils.title_utils import resolve_content_display_title
from app.utils.url_utils import is_http_url

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
    feed_options: list[AssistantFeedOption]


def _clean_option_text(value: str | None, *, limit: int) -> str | None:
    """Normalize optional discovery text for safe structured rendering."""
    cleaned = " ".join((value or "").split())
    return cleaned[:limit] or None


def _build_feed_option(suggestion: FeedDiscoverySuggestion) -> AssistantFeedOption | None:
    """Project one persisted discovery suggestion into an actionable chat option."""
    feed_url = canonicalize_feed_url(suggestion.feed_url or "")
    if not is_http_url(feed_url):
        return None

    raw_type = (suggestion.suggestion_type or "").strip().lower()
    try:
        feed_type = FeedType(normalize_feed_type_alias(raw_type))
    except ValueError:
        logger.warning(
            "Skipping unsupported weekly discovery suggestion type",
            extra={
                "component": "weekly_discovery_chat",
                "operation": "build_feed_option",
                "item_id": str(suggestion.id),
                "context_data": {"suggestion_type": raw_type},
            },
        )
        return None
    feed_format = (
        FeedFormat.RSS if raw_type == "rss" or feed_type != FeedType.ATOM else FeedFormat.ATOM
    )

    site_url = canonicalize_feed_url(suggestion.site_url or feed_url)
    if not is_http_url(site_url):
        site_url = feed_url
    title = _clean_option_text(
        suggestion.title or site_url,
        limit=300,
    )
    if title is None:
        return None

    return AssistantFeedOption(
        id=build_assistant_feed_option_id(feed_url),
        title=title,
        site_url=site_url,
        feed_url=feed_url,
        feed_type=feed_type,
        feed_format=feed_format,
        description=_clean_option_text(suggestion.description, limit=600),
        rationale=_clean_option_text(suggestion.rationale, limit=600),
        evidence_url=site_url,
    )


def _build_feed_options(
    suggestions: list[FeedDiscoverySuggestion],
    *,
    limit: int = 5,
) -> list[AssistantFeedOption]:
    """Return unique renderable options in the persisted suggestion order."""
    options: list[AssistantFeedOption] = []
    seen_feed_urls: set[str] = set()
    for suggestion in suggestions:
        option = _build_feed_option(suggestion)
        if option is None or option.feed_url in seen_feed_urls:
            continue
        seen_feed_urls.add(option.feed_url)
        options.append(option)
        if len(options) >= limit:
            break
    return options


def _user_local_date(user: User, reference_time: datetime | None = None) -> date:
    tz_name = "UTC"
    tz = ZoneInfo(tz_name)
    now = reference_time or datetime.now(UTC)
    return now.astimezone(tz).date()


def _sunday_week_start(local_date: date) -> date:
    days_since_sunday = (local_date.weekday() + 1) % 7
    return local_date - timedelta(days=days_since_sunday)


def _build_seed(db: Session, user: User) -> WeeklyDiscoverySeed:
    user_id = user.id
    if user_id is None:
        raise ValueError("Weekly discovery user must be persisted")
    local_date_value = _user_local_date(user)
    week_start = _sunday_week_start(local_date_value)
    local_date = local_date_value.isoformat()
    recent_rows = (
        db.query(Content)
        .join(ContentReadStatus, ContentReadStatus.content_id == Content.id)
        .filter(ContentReadStatus.user_id == user_id)
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
        .filter(OnboardingDiscoveryRun.user_id == user_id)
        .order_by(OnboardingDiscoveryRun.created_at.desc())
        .first()
    )
    topic_summary = onboarding_run.topic_summary if onboarding_run else None
    inferred_topics = list(onboarding_run.inferred_topics or []) if onboarding_run else []

    suggestion_rows: list[FeedDiscoverySuggestion] = []
    candidate_rows = (
        db.query(FeedDiscoverySuggestion)
        .join(FeedDiscoveryRun, FeedDiscoveryRun.id == FeedDiscoverySuggestion.run_id)
        .filter(
            FeedDiscoveryRun.user_id == user_id,
            FeedDiscoveryRun.status == "completed",
            FeedDiscoverySuggestion.user_id == user_id,
            FeedDiscoverySuggestion.status == "new",
        )
        .order_by(
            FeedDiscoveryRun.created_at.desc(),
            FeedDiscoveryRun.id.desc(),
            FeedDiscoverySuggestion.score.desc().nullslast(),
            FeedDiscoverySuggestion.id.asc(),
        )
        .yield_per(100)
    )
    subscribed_feed_urls = load_active_feed_urls(db, user_id=user_id)
    selected_run_id: int | None = None
    for suggestion in candidate_rows:
        if selected_run_id is not None and suggestion.run_id != selected_run_id:
            break
        if canonicalize_feed_url(suggestion.feed_url or "") in subscribed_feed_urls:
            continue
        if selected_run_id is None:
            selected_run_id = suggestion.run_id
        suggestion_rows.append(suggestion)

    return WeeklyDiscoverySeed(
        local_date=local_date,
        week_key=f"weekly:{week_start.isoformat()}",
        week_label=week_start.isoformat(),
        topic_summary=topic_summary,
        inferred_topics=inferred_topics,
        recent_reads=recent_reads,
        feed_options=_build_feed_options(suggestion_rows),
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
    if seed.feed_options:
        lines.append(
            "Fresh discovery suggestions in canonical numbered order "
            "(ordinal follow-ups refer to this exact order):"
        )
        for index, option in enumerate(seed.feed_options, start=1):
            lines.extend(
                [
                    f"{index}. {option.title}",
                    f"   suggestion_type={option.feed_type.value}",
                    f"   feed_url={option.feed_url}",
                    f"   site_url={option.site_url}",
                ]
            )
            if option.rationale:
                lines.append(f"   rationale={option.rationale}")
    return "\n".join(lines)


def _build_seed_message(seed: WeeklyDiscoverySeed) -> str:
    intro = f"Here are a few things worth exploring for the week of {seed.week_label}."
    if seed.feed_options:
        lines = [intro, "", "Fresh suggestions:"]
        for index, option in enumerate(seed.feed_options, start=1):
            lines.append(f"{index}. {option.title}")
            if option.rationale:
                lines.append(f"   Why it stands out: {option.rationale}")
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


def _unengaged_seed_message(db: Session, session: ChatSession) -> ChatMessage | None:
    """Return the sole assistant seed row when the session has no user activity."""
    session_id = _require_session_id(session)
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.asc())
        .limit(2)
        .all()
    )
    if len(messages) != 1:
        return None

    seed_message = messages[0]
    if (
        seed_message.status != MessageProcessingStatus.COMPLETED.value
        or seed_message.processing_context is not None
    ):
        return None

    if _assistant_seed_text(seed_message) is None:
        return None
    return seed_message


def _assistant_seed_text(seed_message: ChatMessage) -> str | None:
    """Decode the one-part assistant response that identifies an untouched seed."""
    serialized_messages = seed_message.message_list
    if serialized_messages is None:
        return None
    try:
        model_messages = ModelMessagesTypeAdapter.validate_json(serialized_messages)
    except Exception:  # noqa: BLE001
        return None
    if len(model_messages) != 1 or not isinstance(model_messages[0], ModelResponse):
        return None
    parts = model_messages[0].parts
    if len(parts) != 1 or not isinstance(parts[0], TextPart):
        return None
    return parts[0].content


def _seed_render_metadata(seed: WeeklyDiscoverySeed) -> ChatMessageRenderMetadata | None:
    """Build render metadata only when the seed has actionable options."""
    if not seed.feed_options:
        return None
    return ChatMessageRenderMetadata(feed_options=seed.feed_options)


def _reproject_unengaged_session(
    db: Session,
    *,
    session: ChatSession,
    seed: WeeklyDiscoverySeed,
) -> bool:
    """Refresh an untouched weekly seed without rewriting an active conversation."""
    seed_message = _unengaged_seed_message(db, session)
    if seed_message is None:
        return False

    context_snapshot = _build_context_snapshot(seed)
    assistant_text = _build_seed_message(seed)
    render_metadata = _seed_render_metadata(seed)
    serialized_metadata = (
        render_metadata.model_dump(mode="json") if render_metadata is not None else None
    )
    existing_text = _assistant_seed_text(seed_message)
    if existing_text is None:
        return False
    if (
        session.context_snapshot == context_snapshot
        and existing_text == assistant_text
        and seed_message.render_metadata == serialized_metadata
    ):
        return False

    now = datetime.now(UTC)
    session.context_snapshot = context_snapshot
    session.updated_at = now
    session.last_message_at = now
    db.delete(seed_message)
    db.flush()
    seed_assistant_message(
        db,
        session_id=_require_session_id(session),
        assistant_text=assistant_text,
        render_metadata=render_metadata,
        commit=False,
    )
    return True


def ensure_weekly_discovery_session(
    db: Session,
    *,
    user_id: int,
) -> ChatSession | None:
    """Create or safely refresh the current weekly discovery chat session."""
    user = (
        db.query(User)
        .filter(User.id == user_id, User.has_completed_onboarding.is_(True))
        .with_for_update()
        .first()
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
        try:
            reprojected = _reproject_unengaged_session(db, session=existing, seed=seed)
            if reprojected:
                db.commit()
                db.refresh(existing)
                logger.info(
                    "Reprojected unengaged weekly discovery session",
                    extra={
                        "component": "weekly_discovery_chat",
                        "operation": "reproject_session",
                        "item_id": str(user_id),
                        "context_data": {
                            "session_id": existing.id,
                            "local_date": seed.local_date,
                        },
                    },
                )
        except Exception:
            db.rollback()
            raise
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
    try:
        db.add(session)
        db.flush()
        seed_assistant_message(
            db,
            session_id=_require_session_id(session),
            assistant_text=_build_seed_message(seed),
            render_metadata=_seed_render_metadata(seed),
            commit=False,
        )
        session.last_message_at = datetime.now(UTC)
        db.commit()
    except Exception:
        db.rollback()
        raise
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
