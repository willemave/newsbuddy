"""Atomic one-time refresh-token consumption."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.models.db import ConsumedRefreshToken


def consume_refresh_token(
    db: Session,
    *,
    raw_token: str,
    payload: dict[str, Any],
    user_id: int,
) -> bool:
    """Consume one refresh token exactly once in the caller's transaction."""

    expires_at = _expiration_datetime(payload.get("exp"))
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    db.execute(
        delete(ConsumedRefreshToken).where(ConsumedRefreshToken.expires_at < datetime.now(UTC))
    )
    inserted = db.execute(
        postgresql_insert(ConsumedRefreshToken)
        .values(
            token_hash=token_hash,
            user_id=user_id,
            expires_at=expires_at,
        )
        .on_conflict_do_nothing(index_elements=[ConsumedRefreshToken.token_hash])
        .returning(ConsumedRefreshToken.token_hash)
    ).scalar_one_or_none()
    return inserted is not None


def _expiration_datetime(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Refresh token is missing a valid expiry")
    return datetime.fromtimestamp(float(value), tz=UTC)
