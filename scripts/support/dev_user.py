"""Reusable local developer-user scenarios and diagnostics."""

from __future__ import annotations

import random
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.contracts import ContentStatus, ContentType, TaskType
from app.models.db import (
    BriefingLens,
    BriefingPendingSource,
    BriefingSegment,
    BriefingState,
    Content,
    ContentDiscussion,
    ContentKnowledgeSave,
    ContentReadStatus,
    ContentStatusEntry,
    NewsItem,
    NewsItemDiscussion,
    NewsItemReadStatus,
    OnboardingFirstEditionRun,
    OnboardingFirstEditionSource,
    ProcessingTask,
    User,
)
from app.services.briefing.presentation import get_briefing_index
from app.services.briefing.refresh import run_briefing_refresh, status_counts
from scripts.generate_test_data import (
    ArticleGenerator,
    NewsGenerator,
    PodcastGenerator,
    insert_test_data,
    write_placeholder_image,
)

DevProfile = Literal["showcase", "onboarding"]
BriefingMode = Literal["llm", "deterministic", "none"]

ONBOARDING_STATES = (
    "initial",
    "early",
    "mid",
    "partial_failure",
    "delayed",
    "ready",
    "resumed",
    "completed",
)
ONBOARDING_SOURCE_NAMES = ("Techmeme", "Stratechery", "Decoder", "Hacker News")
ONBOARDING_SOURCE_ITEM_COUNTS = (28, 12, 9, 34)
ONBOARDING_LENS_KEY = "start-here-technology"

PROFILE_APPLE_ID = "debug.newsly.showcase"
PROFILE_EMAIL = "debug+showcase@newsly.local"
PROFILE_NAME = "Newsly Showcase"
PROFILE_URL_PREFIX = "https://example.com/newsly-dev/showcase"


def setup_showcase_user(
    db: Session,
    *,
    briefing_mode: BriefingMode,
) -> dict[str, Any]:
    """Create one stable, richly populated local user and return diagnostics."""

    user = _upsert_showcase_user(db)
    user_id = _required_id(user.id, "user.id")
    _reset_showcase_data(db, user_id=user_id)

    random.seed("newsly-showcase-v1")
    data = _showcase_content()
    inserted_ids = insert_test_data(db, data, user_ids=[user_id])
    _seed_read_and_saved_states(db, user_id=user_id, content_ids=inserted_ids)
    db.commit()

    if briefing_mode != "none":
        settings = get_settings().model_copy(
            update={
                "briefing_enabled_user_ids": [user_id],
                "briefing_window_min": 1,
                "briefing_debounce_seconds": 0,
                "briefing_pending_max_age_seconds": 60,
            }
        )
        run_briefing_refresh(
            db,
            user_id=user_id,
            mode="full",
            use_llm=briefing_mode == "llm",
            settings=settings,
        )
        _backfill_briefing_segment_images(db, user_ids=[user_id])
        db.commit()

    return dev_user_status(db, user=user)


def setup_onboarding_user(db: Session, *, state: str) -> dict[str, Any]:
    """Reset the stable developer user into a deterministic Start Here state."""

    if state not in ONBOARDING_STATES:
        raise ValueError(f"Unsupported onboarding state: {state}")
    user = _upsert_showcase_user(db)
    user_id = _required_id(user.id, "user.id")
    _reset_showcase_data(db, user_id=user_id)
    onboarding = _seed_onboarding_state(db, user=user, state=state)
    db.commit()
    result = dev_user_status(db, user=user, profile="onboarding")
    result["onboarding"] = onboarding
    return result


def find_showcase_user(db: Session) -> User | None:
    return db.query(User).filter(User.apple_id == PROFILE_APPLE_ID).first()


def dev_user_status(
    db: Session,
    *,
    user: User,
    profile: DevProfile | None = None,
) -> dict[str, Any]:
    user_id = _required_id(user.id, "user.id")
    content_rows = (
        db.query(Content.content_type, Content.id)
        .join(ContentStatusEntry, ContentStatusEntry.content_id == Content.id)
        .filter(ContentStatusEntry.user_id == user_id)
        .filter(ContentStatusEntry.status == "inbox")
        .all()
    )
    inbox_counts = Counter(str(content_type) for content_type, _content_id in content_rows)
    owned_news_count = db.query(NewsItem).filter(NewsItem.owner_user_id == user_id).count()
    read_content_count = (
        db.query(ContentReadStatus).filter(ContentReadStatus.user_id == user_id).count()
    )
    read_news_count = (
        db.query(NewsItemReadStatus).filter(NewsItemReadStatus.user_id == user_id).count()
    )
    saved_count = (
        db.query(ContentKnowledgeSave).filter(ContentKnowledgeSave.user_id == user_id).count()
    )
    if profile is None:
        has_onboarding_run = (
            db.query(OnboardingFirstEditionRun.id)
            .filter(OnboardingFirstEditionRun.user_id == user_id)
            .first()
            is not None
        )
        profile = (
            "onboarding"
            if has_onboarding_run or (not content_rows and owned_news_count == 0)
            else "showcase"
        )
    index = get_briefing_index(db, user_id=user_id)
    latest_task = _latest_briefing_task(db, user_id=user_id)
    return {
        "profile": profile,
        "user": {
            "id": user_id,
            "email": user.email,
            "full_name": user.full_name,
            "is_active": bool(user.is_active),
            "reading_experience": user.reading_experience,
            "has_completed_onboarding": bool(user.has_completed_onboarding),
            "has_completed_new_user_tutorial": bool(user.has_completed_new_user_tutorial),
        },
        "content": {
            "articles": inbox_counts[ContentType.ARTICLE.value],
            "podcasts": inbox_counts[ContentType.PODCAST.value],
            "news": owned_news_count,
            "read": read_content_count + read_news_count,
            "saved": saved_count,
        },
        "briefing": {
            "version": index.version,
            "counts": status_counts(db, user_id=user_id),
            "lenses": [
                {
                    "key": lens.key,
                    "title": lens.title,
                    "tier": lens.tier,
                    "unread_sources": lens.unread_source_count,
                    "segments": lens.segment_count,
                }
                for lens in index.lenses
            ],
            "latest_task": latest_task,
        },
    }


def _upsert_showcase_user(db: Session) -> User:
    user = find_showcase_user(db)
    if user is None:
        user = User(apple_id=PROFILE_APPLE_ID, email=PROFILE_EMAIL)
        db.add(user)
    user.email = PROFILE_EMAIL
    user.full_name = PROFILE_NAME
    user.is_active = True
    user.is_admin = False
    user.has_completed_onboarding = True
    user.has_completed_new_user_tutorial = True
    user.reading_experience = "briefing"
    db.flush()
    return user


def _showcase_content() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for _index in range(8):
        items.append(
            ArticleGenerator.generate(
                url_base=f"{PROFILE_URL_PREFIX}/article",
                status=ContentStatus.COMPLETED.value,
                summary_format="longform_artifact",
            )
        )
    for _index in range(6):
        items.append(
            PodcastGenerator.generate(
                url_base=f"{PROFILE_URL_PREFIX}/podcast",
                status=ContentStatus.COMPLETED.value,
                summary_format="longform_artifact",
            )
        )
    for index in range(24):
        items.append(
            NewsGenerator.generate(
                url_base=f"{PROFILE_URL_PREFIX}/news",
                status=ContentStatus.COMPLETED.value,
                day_offset=index % 4,
            )
        )
    for item in items:
        item["classification"] = "to_read"
        metadata = item.get("content_metadata")
        if not isinstance(metadata, dict):
            continue
        summary = metadata.get("summary")
        if isinstance(summary, dict):
            summary["classification"] = "to_read"
    return items


def _seed_read_and_saved_states(
    db: Session,
    *,
    user_id: int,
    content_ids: list[int],
) -> None:
    rows = db.query(Content).filter(Content.id.in_(content_ids)).order_by(Content.id.asc()).all()
    longform = [
        row
        for row in rows
        if row.content_type in {ContentType.ARTICLE.value, ContentType.PODCAST.value}
    ]
    for content in longform[-3:]:
        db.add(ContentReadStatus(user_id=user_id, content_id=content.id))
    for content in longform[1:4]:
        db.add(ContentKnowledgeSave(user_id=user_id, content_id=content.id))
    db.flush()


def _seed_onboarding_state(
    db: Session,
    *,
    user: User,
    state: str,
) -> dict[str, object]:
    now = datetime.now(UTC).replace(tzinfo=None)
    user.reading_experience = "briefing"
    user.has_completed_onboarding = True
    user.has_completed_new_user_tutorial = state == "completed"

    briefing_state = BriefingState(
        user_id=user.id,
        version=1,
        masthead_title="Briefing",
        masthead_deck="Your first edition is taking shape.",
    )
    db.add(briefing_state)

    if state == "completed":
        db.flush()
        return {"state": state, "run_id": None}

    completed_count = 0
    if state == "early":
        completed_count = 1
    elif state in {"mid", "resumed"}:
        completed_count = 2
    elif state == "partial_failure":
        completed_count = 3
    elif state in {"delayed", "ready"}:
        completed_count = len(ONBOARDING_SOURCE_NAMES)

    run = OnboardingFirstEditionRun(
        user_id=user.id,
        status="active",
        revision=completed_count + 1,
    )
    db.add(run)
    db.flush()
    for position, display_name in enumerate(ONBOARDING_SOURCE_NAMES):
        is_complete = position < completed_count
        is_unavailable = state == "partial_failure" and position == 2
        source_status = "queued"
        if is_complete:
            source_status = "unavailable" if is_unavailable else "processed"
        item_count = (
            ONBOARDING_SOURCE_ITEM_COUNTS[position] if is_complete and not is_unavailable else 0
        )
        db.add(
            OnboardingFirstEditionSource(
                run_id=run.id,
                source_key=f"fixture:{position}",
                display_name=display_name,
                source_kind="fixture",
                position=position,
                status=source_status,
                processed_item_count=item_count,
                completed_at=now if is_complete else None,
            )
        )

    ready_keys: list[str] = []
    if state == "ready":
        ready_keys.append(ONBOARDING_LENS_KEY)
        lens = BriefingLens(
            user_id=user.id,
            key=ONBOARDING_LENS_KEY,
            tier="news",
            title="Technology",
            deck="The first stories ready from your sources.",
            position=20,
            status="active",
        )
        db.add(lens)
        db.flush()
        db.add(
            BriefingSegment(
                lens_id=lens.id,
                user_id=user.id,
                blocks=[
                    {
                        "type": "passage",
                        "weight": "lead",
                        "paragraphs": [
                            {
                                "runs": [
                                    {
                                        "kind": "text",
                                        "text": (
                                            "Your first technology briefing is ready. "
                                            "Future stories will continue to append here."
                                        ),
                                    }
                                ]
                            }
                        ],
                    }
                ],
                markdown_raw="Your first technology briefing is ready.",
                narration_text="Your first technology briefing is ready.",
                source_keys=[],
                status="active",
                model="fixture",
                prompt_version="fixture-v1",
                created_at=now,
            )
        )

    db.flush()
    return {
        "state": state,
        "run_id": run.id,
        "completed_sources": completed_count,
        "ready_category_keys": ready_keys,
    }


def _reset_showcase_data(db: Session, *, user_id: int) -> None:
    content_ids = [
        int(content_id)
        for (content_id,) in db.query(Content.id)
        .filter(Content.url.like(f"{PROFILE_URL_PREFIX}%"))
        .all()
    ]
    news_ids = (
        [
            int(news_id)
            for (news_id,) in db.query(NewsItem.id)
            .filter(NewsItem.legacy_content_id.in_(content_ids))
            .all()
        ]
        if content_ids
        else []
    )

    db.query(BriefingSegment).filter(BriefingSegment.user_id == user_id).delete()
    db.query(BriefingPendingSource).filter(BriefingPendingSource.user_id == user_id).delete()
    db.query(BriefingLens).filter(BriefingLens.user_id == user_id).delete()
    db.query(BriefingState).filter(BriefingState.user_id == user_id).delete()

    run_ids = [
        int(run_id)
        for (run_id,) in db.query(OnboardingFirstEditionRun.id)
        .filter(OnboardingFirstEditionRun.user_id == user_id)
        .all()
    ]
    if run_ids:
        db.query(OnboardingFirstEditionSource).filter(
            OnboardingFirstEditionSource.run_id.in_(run_ids)
        ).delete(synchronize_session=False)
        db.query(OnboardingFirstEditionRun).filter(
            OnboardingFirstEditionRun.id.in_(run_ids)
        ).delete(synchronize_session=False)

    task_ids = [
        int(task.id)
        for task in db.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.BRIEFING_REFRESH.value)
        .all()
        if task.id is not None
        and isinstance(task.payload, dict)
        and task.payload.get("user_id") == user_id
    ]
    if task_ids:
        db.query(ProcessingTask).filter(ProcessingTask.id.in_(task_ids)).delete(
            synchronize_session=False
        )

    if news_ids:
        db.query(NewsItemReadStatus).filter(NewsItemReadStatus.news_item_id.in_(news_ids)).delete(
            synchronize_session=False
        )
        db.query(NewsItemDiscussion).filter(NewsItemDiscussion.news_item_id.in_(news_ids)).delete(
            synchronize_session=False
        )
        db.query(NewsItem).filter(NewsItem.id.in_(news_ids)).delete(synchronize_session=False)

    if content_ids:
        db.query(ContentReadStatus).filter(ContentReadStatus.content_id.in_(content_ids)).delete(
            synchronize_session=False
        )
        db.query(ContentKnowledgeSave).filter(
            ContentKnowledgeSave.content_id.in_(content_ids)
        ).delete(synchronize_session=False)
        db.query(ContentStatusEntry).filter(ContentStatusEntry.content_id.in_(content_ids)).delete(
            synchronize_session=False
        )
        db.query(ContentDiscussion).filter(ContentDiscussion.content_id.in_(content_ids)).delete(
            synchronize_session=False
        )
        db.query(Content).filter(Content.id.in_(content_ids)).delete(synchronize_session=False)
        _remove_generated_images(content_ids)
    db.commit()


def _remove_generated_images(content_ids: list[int]) -> None:
    base_dir = Path(get_settings().images_base_dir)
    for content_id in content_ids:
        (base_dir / "content" / f"{content_id}.png").unlink(missing_ok=True)
        (base_dir / "thumbnails" / f"{content_id}.png").unlink(missing_ok=True)


def _backfill_briefing_segment_images(db: Session, *, user_ids: list[int]) -> int:
    """Ensure long-form sources used by the seeded Briefing have local images."""

    key_rows = (
        db.query(BriefingSegment.source_keys)
        .filter(BriefingSegment.user_id.in_(user_ids))
        .filter(BriefingSegment.status == "active")
        .all()
    )
    content_ids: set[int] = set()
    for (keys,) in key_rows:
        for key in keys or []:
            kind, _, raw_id = str(key).partition(":")
            if kind == "content" and raw_id.isdigit():
                content_ids.add(int(raw_id))

    base_dir = Path(get_settings().images_base_dir)
    written = 0
    for content_id in sorted(content_ids):
        content_path = base_dir / "content" / f"{content_id}.png"
        if not content_path.exists():
            write_placeholder_image(content_path, size=(1200, 675), seed=content_id)
            written += 1
        thumb_path = base_dir / "thumbnails" / f"{content_id}.png"
        if not thumb_path.exists():
            write_placeholder_image(thumb_path, size=(200, 200), seed=content_id)
    return written


def _latest_briefing_task(db: Session, *, user_id: int) -> dict[str, Any] | None:
    tasks = (
        db.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.BRIEFING_REFRESH.value)
        .order_by(ProcessingTask.id.desc())
        .all()
    )
    task = next(
        (
            candidate
            for candidate in tasks
            if isinstance(candidate.payload, dict) and candidate.payload.get("user_id") == user_id
        ),
        None,
    )
    if task is None:
        return None
    return {
        "id": task.id,
        "status": task.status,
        "mode": task.payload.get("mode") if isinstance(task.payload, dict) else None,
        "error": task.error_message,
    }


def _required_id(value: int | None, field: str) -> int:
    if value is None:
        raise ValueError(f"{field} was not persisted")
    return int(value)
