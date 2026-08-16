from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.core.settings import Settings, get_settings
from app.models.db import BriefingState


def ensure_briefing_state(
    db: Session,
    *,
    user_id: int,
    settings: Settings | None = None,
) -> BriefingState:
    """Return the durable per-user Briefing state, creating it when absent."""

    state = db.query(BriefingState).filter(BriefingState.user_id == user_id).first()
    if state is not None:
        return state

    resolved_settings = settings or get_settings()
    db.execute(
        postgresql_insert(BriefingState)
        .values(
            user_id=user_id,
            version=0,
            masthead_title=resolved_settings.briefing_masthead_title,
            masthead_deck="A fresh edition will appear as unread sources arrive.",
        )
        .on_conflict_do_nothing(index_elements=[BriefingState.user_id])
    )
    return db.query(BriefingState).filter(BriefingState.user_id == user_id).one()


def lock_briefing_state(
    db: Session,
    *,
    user_id: int,
    settings: Settings | None = None,
) -> BriefingState:
    """Lock and refresh the state row that serializes visible Briefing mutations."""

    query = (
        db.query(BriefingState)
        .filter(BriefingState.user_id == user_id)
        .populate_existing()
        .with_for_update()
    )
    state = query.one_or_none()
    if state is not None:
        return state
    ensure_briefing_state(db, user_id=user_id, settings=settings)
    return query.one()
