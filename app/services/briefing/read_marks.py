from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session, load_only

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.models.db import (
    BriefingLens,
    BriefingSegment,
    BriefingState,
    NewsItem,
    NewsItemReadStatus,
)
from app.repositories import read_status_repository
from app.services.briefing.eligibility import briefing_enabled_user_ids
from app.services.briefing.source_keys import build_source_key, parse_source_key
from app.services.briefing.sources import read_source_keys_for

logger = get_logger(__name__)


@dataclass(frozen=True)
class BriefingReadMarkResult:
    marked: int
    retired: int
    version: int


def mark_briefing_lens_read(
    db: Session,
    *,
    user_id: int,
    lens_key: str,
) -> BriefingReadMarkResult | None:
    lens_id = db.execute(
        select(BriefingLens.id).where(
            BriefingLens.user_id == user_id,
            BriefingLens.key == lens_key,
            BriefingLens.status == "active",
        )
    ).scalar_one_or_none()
    if lens_id is None:
        return None

    segment_source_keys = db.execute(
        select(BriefingSegment.source_keys).where(
            BriefingSegment.lens_id == lens_id,
            BriefingSegment.user_id == user_id,
            BriefingSegment.status.in_(("active", "degraded")),
        )
    ).scalars()
    source_keys = sorted(
        {
            str(source_key)
            for segment_keys in segment_source_keys
            for source_key in (segment_keys or [])
        }
    )
    read_keys = read_source_keys_for(db, user_id=user_id, source_keys=source_keys)
    source_keys_to_mark: list[str] = []
    for source_key in source_keys:
        parsed = parse_source_key(source_key)
        if source_key not in read_keys or (parsed is not None and parsed.kind == "news"):
            source_keys_to_mark.append(source_key)
    return mark_briefing_sources_read(
        db,
        user_id=user_id,
        source_keys=source_keys_to_mark,
    )


def mark_briefing_sources_read(
    db: Session,
    *,
    user_id: int,
    source_keys: list[str],
) -> BriefingReadMarkResult:
    parsed = [parse_source_key(key) for key in source_keys]
    content_ids = sorted({key.source_id for key in parsed if key and key.kind == "content"})
    news_ids = sorted({key.source_id for key in parsed if key and key.kind == "news"})

    marked = 0
    if content_ids:
        # commands.mark_read.bulk_mark_read rejects the whole batch on unknown ids;
        # scroll-driven briefing batches must tolerate stale keys, so mark via the
        # repository and log the failures instead.
        content_marked, failed_content_ids = read_status_repository.mark_contents_as_read(
            db,
            content_ids,
            user_id,
        )
        marked += content_marked
        if failed_content_ids:
            logger.warning(
                "Briefing read-marks skipped unknown content ids",
                extra={"user_id": user_id, "failed_content_ids": failed_content_ids},
            )
    if news_ids:
        marked += _mark_briefing_news_items_read(
            db,
            user_id=user_id,
            news_item_ids=news_ids,
        )

    retired = retire_read_segments(db, user_id=user_id)
    state = _state_for_update(db, user_id=user_id)
    version = int(state.version or 0)
    if marked or retired:
        version += 1
        state.version = version
    db.flush()
    return BriefingReadMarkResult(marked=marked, retired=retired, version=version)


def _mark_briefing_news_items_read(
    db: Session,
    *,
    user_id: int,
    news_item_ids: list[int],
) -> int:
    # A source's active feed identity can change after its immutable Briefing
    # segment is composed. Segment ownership authorizes the original key; both
    # that row and its current representative are persisted as read.
    requested_ids = set(news_item_ids)
    authorized_ids = _briefing_owned_news_item_ids(
        db,
        user_id=user_id,
        news_item_ids=requested_ids,
    )
    rejected_ids = sorted(requested_ids - authorized_ids)
    if rejected_ids:
        logger.warning(
            "Briefing read-marks skipped news ids outside the user's segments",
            extra={"user_id": user_id, "news_item_ids": rejected_ids},
        )
    if not authorized_ids:
        return 0

    rows = db.execute(
        select(NewsItem.id, NewsItem.representative_news_item_id).where(
            NewsItem.id.in_(authorized_ids)
        )
    ).all()
    found_ids = {int(news_item_id) for news_item_id, _representative_id in rows}
    missing_ids = sorted(authorized_ids - found_ids)
    if missing_ids:
        logger.warning(
            "Briefing read-marks skipped missing news ids",
            extra={"user_id": user_id, "news_item_ids": missing_ids},
        )
    target_ids = {
        int(target_id)
        for news_item_id, representative_id in rows
        for target_id in (news_item_id, representative_id)
        if target_id is not None
    }
    if not target_ids:
        return 0

    timestamp = datetime.now(UTC).replace(tzinfo=None)
    inserted_ids = set(
        db.execute(
            postgresql_insert(NewsItemReadStatus)
            .values(
                [
                    {
                        "user_id": user_id,
                        "news_item_id": news_item_id,
                        "read_at": timestamp,
                        "created_at": timestamp,
                    }
                    for news_item_id in sorted(target_ids)
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[
                    NewsItemReadStatus.user_id,
                    NewsItemReadStatus.news_item_id,
                ]
            )
            .returning(NewsItemReadStatus.news_item_id)
        ).scalars()
    )
    return len(inserted_ids & authorized_ids)


def _briefing_owned_news_item_ids(
    db: Session,
    *,
    user_id: int,
    news_item_ids: set[int],
) -> set[int]:
    if not news_item_ids:
        return set()
    source_keys = {
        news_item_id: build_source_key("news", news_item_id) for news_item_id in news_item_ids
    }
    matching_segment_keys = db.execute(
        select(BriefingSegment.source_keys).where(
            BriefingSegment.user_id == user_id,
            or_(
                *(
                    BriefingSegment.source_keys.contains([source_key])
                    for source_key in source_keys.values()
                )
            ),
        )
    ).scalars()
    owned_keys = {
        str(source_key)
        for segment_keys in matching_segment_keys
        for source_key in (segment_keys or [])
    }
    return {
        news_item_id for news_item_id, source_key in source_keys.items() if source_key in owned_keys
    }


def retire_read_segments(db: Session, *, user_id: int) -> int:
    segments = (
        db.query(BriefingSegment)
        .options(
            load_only(
                BriefingSegment.id,
                BriefingSegment.source_keys,
                BriefingSegment.status,
            )
        )
        .filter(BriefingSegment.user_id == user_id)
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .all()
    )
    active_source_keys = sorted(
        {str(key) for segment in segments for key in (segment.source_keys or [])}
    )
    read_keys = read_source_keys_for(db, user_id=user_id, source_keys=active_source_keys)
    retired = 0
    for segment in segments:
        source_keys = {str(key) for key in (segment.source_keys or [])}
        if source_keys and source_keys.issubset(read_keys):
            segment.status = "retired"
            retired += 1
    return retired


def bump_briefing_version_for_news_item(
    db: Session,
    *,
    news_item_id: int,
    settings: Settings | None = None,
) -> bool:
    settings = settings or get_settings()
    source_key = build_source_key("news", int(news_item_id))
    matching_user_ids = (
        db.execute(
            select(BriefingSegment.user_id)
            .where(BriefingSegment.status.in_(("active", "degraded")))
            .where(BriefingSegment.source_keys.contains([source_key]))
            .distinct()
        )
        .scalars()
        .all()
    )
    candidate_user_ids = {int(user_id) for user_id in matching_user_ids if user_id is not None}
    enabled_user_ids = briefing_enabled_user_ids(
        db,
        candidate_user_ids=candidate_user_ids,
        settings=settings,
    )
    if not enabled_user_ids:
        return False

    for user_id in sorted(enabled_user_ids):
        state = _state_for_update(db, user_id=user_id)
        state.version = int(state.version or 0) + 1
    db.flush()
    return True


def _state_for_update(db: Session, *, user_id: int) -> BriefingState:
    state = db.query(BriefingState).filter(BriefingState.user_id == user_id).first()
    if state is None:
        state = BriefingState(
            user_id=user_id,
            version=0,
            masthead_title="The Unread Times",
            masthead_deck="A fresh edition will appear as unread sources arrive.",
        )
        db.add(state)
        db.flush()
    return state
