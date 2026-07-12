"""Canonical policy for users whose Briefing is maintained by the backend."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.core.settings import Settings, get_settings
from app.models.db import User


def briefing_enabled_user_ids(
    db: Session,
    *,
    candidate_user_ids: Iterable[int] | None = None,
    settings: Settings | None = None,
) -> set[int]:
    settings = settings or get_settings()
    candidates = {int(user_id) for user_id in candidate_user_ids or []}
    configured = {int(user_id) for user_id in settings.briefing_enabled_user_ids}
    if candidate_user_ids is not None:
        configured.intersection_update(candidates)
        candidates.difference_update(configured)
        if not candidates:
            return configured

    query = db.query(User.id).filter(User.is_active.is_(True))
    if candidate_user_ids is not None:
        query = query.filter(User.id.in_(candidates))
    configured.update(int(user_id) for (user_id,) in query.all())
    return configured


def is_briefing_enabled_for_user(
    db: Session,
    *,
    user_id: int,
    settings: Settings | None = None,
) -> bool:
    return user_id in briefing_enabled_user_ids(
        db,
        candidate_user_ids=[user_id],
        settings=settings,
    )
