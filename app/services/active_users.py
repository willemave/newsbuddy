"""Transactional guards for user-owned side effects."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.db import User


def lock_active_user(db: Session, user_id: object) -> int | None:
    """Lock and return an active user ID, or ``None`` when it is no longer valid.

    The shared row lock serializes user-owned writes with account deletion, which
    takes an exclusive lock on the same row before marking or removing the user.
    Callers must keep the transaction open until their side effects are durable.
    """
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        return None

    row = (
        db.query(User.id)
        .filter(User.id == user_id, User.is_active.is_(True))
        .with_for_update(read=True)
        .one_or_none()
    )
    return int(row[0]) if row is not None else None
