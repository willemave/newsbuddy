"""Focused state-contract tests for refresh-token rotation replay."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.security import create_refresh_token, verify_token
from app.models.db import ConsumedRefreshToken
from app.services.refresh_token_rotation import REFRESH_REPLAY_TTL, rotate_refresh_token


def test_expired_refresh_replay_clears_payload_but_keeps_consumption(
    db_session: Session,
    user_factory,
) -> None:
    user = user_factory(
        apple_id="001234.expired-replay",
        email="expired-replay@icloud.com",
        is_active=True,
    )
    refresh_token = create_refresh_token(user.id)
    payload = verify_token(refresh_token)
    attempt_id = str(uuid4())
    initial_time = datetime.now(UTC)

    first = rotate_refresh_token(
        db_session,
        raw_token=refresh_token,
        payload=payload,
        user_id=user.id,
        attempt_id=attempt_id,
        now=initial_time,
    )
    db_session.commit()
    expired = rotate_refresh_token(
        db_session,
        raw_token=refresh_token,
        payload=payload,
        user_id=user.id,
        attempt_id=attempt_id,
        now=initial_time + REFRESH_REPLAY_TTL + timedelta(seconds=1),
    )
    db_session.commit()

    assert first is not None
    assert expired is None
    record = db_session.query(ConsumedRefreshToken).one()
    assert record.attempt_id is None
    assert record.replay_payload_encrypted is None
    assert record.replay_expires_at is None
