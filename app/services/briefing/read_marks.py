from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.db import BriefingSegment, BriefingState
from app.repositories import read_status_repository
from app.services.briefing.source_keys import parse_source_key
from app.services.briefing.sources import read_source_keys_for
from app.services.news_feed import bulk_mark_news_items_read

logger = get_logger(__name__)


@dataclass(frozen=True)
class BriefingReadMarkResult:
    marked: int
    version: int


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
        news_result = bulk_mark_news_items_read(db, user_id=user_id, news_item_ids=news_ids)
        marked += news_result.marked_count

    retired = retire_read_segments(db, user_id=user_id)
    state = _state_for_update(db, user_id=user_id)
    version = int(state.version or 0)
    if marked or retired:
        version += 1
        state.version = version
    db.flush()
    return BriefingReadMarkResult(marked=marked, version=version)


def retire_read_segments(db: Session, *, user_id: int) -> int:
    segments = (
        db.query(BriefingSegment)
        .filter(BriefingSegment.user_id == user_id)
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .all()
    )
    active_source_keys = sorted(
        {
            str(key)
            for segment in segments
            for key in (segment.source_keys or [])
        }
    )
    read_keys = read_source_keys_for(db, user_id=user_id, source_keys=active_source_keys)
    retired = 0
    for segment in segments:
        source_keys = {str(key) for key in (segment.source_keys or [])}
        if source_keys and source_keys.issubset(read_keys):
            segment.status = "retired"
            retired += 1
    return retired


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
