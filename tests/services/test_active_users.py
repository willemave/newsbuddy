"""Tests for transactional active-user guards."""

import pytest
from sqlalchemy.exc import OperationalError

from app.models.db import User
from app.services.active_users import lock_active_user


def test_lock_active_user_rejects_missing_and_inactive_users(db_session, user_factory) -> None:
    inactive_user = user_factory(is_active=False)

    assert lock_active_user(db_session, inactive_user.id) is None
    assert lock_active_user(db_session, inactive_user.id + 1000) is None
    assert lock_active_user(db_session, True) is None


def test_lock_active_user_serializes_with_exclusive_account_lock(
    db_session_factory,
    user_factory,
) -> None:
    user = user_factory()
    user_id = user.id
    assert user_id is not None
    guarded_session = db_session_factory()
    deletion_session = db_session_factory()
    try:
        assert lock_active_user(guarded_session, user_id) == user_id

        with pytest.raises(OperationalError):
            (
                deletion_session.query(User)
                .filter(User.id == user_id)
                .with_for_update(nowait=True)
                .one()
            )
        deletion_session.rollback()

        guarded_session.commit()
        locked_user = (
            deletion_session.query(User)
            .filter(User.id == user_id)
            .with_for_update(nowait=True)
            .one()
        )
        assert locked_user.id == user_id
    finally:
        guarded_session.close()
        deletion_session.close()
