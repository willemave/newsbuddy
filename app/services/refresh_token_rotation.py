"""Atomic one-time refresh-token rotation with bounded replay retrieval."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.core.security import create_access_token, create_refresh_token
from app.core.settings import get_settings
from app.models.db import ConsumedRefreshToken

REFRESH_REPLAY_TTL = timedelta(minutes=10)
_REPLAY_KEY_CONTEXT = b"newsly:refresh-token-replay:v1\0"


@dataclass(frozen=True)
class RotatedRefreshTokens:
    """One newly issued or replay-retrieved credential pair."""

    access_token: str
    refresh_token: str
    replayed: bool


class RefreshTokenReplayError(RuntimeError):
    """Stored replay material exists but cannot be read safely."""


def rotate_refresh_token(
    db: Session,
    *,
    raw_token: str,
    payload: dict[str, Any],
    user_id: int,
    attempt_id: str | None,
    now: datetime | None = None,
) -> RotatedRefreshTokens | None:
    """Rotate once, or retrieve the same result for one unexpired attempt ID."""

    current_time = now or datetime.now(UTC)
    expires_at = _expiration_datetime(payload.get("exp"))
    token_digest = hashlib.sha256(raw_token.encode("utf-8")).digest()
    token_hash = token_digest.hex()
    db.execute(delete(ConsumedRefreshToken).where(ConsumedRefreshToken.expires_at < current_time))
    db.execute(
        update(ConsumedRefreshToken)
        .where(
            ConsumedRefreshToken.replay_expires_at.is_not(None),
            ConsumedRefreshToken.replay_expires_at <= current_time,
        )
        .values(
            attempt_id=None,
            replay_payload_encrypted=None,
            replay_expires_at=None,
        )
    )
    # The table's unique key remains the final safety fence. This transaction lock
    # additionally lets identical attempts observe the committed winner payload
    # without generating a second candidate pair first.
    lock_key = int.from_bytes(token_digest[:8], byteorder="big", signed=True)
    db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    existing = db.execute(
        select(ConsumedRefreshToken).where(ConsumedRefreshToken.token_hash == token_hash)
    ).scalar_one_or_none()
    if existing is not None:
        return _replayed_tokens(existing, attempt_id=attempt_id, current_time=current_time)

    inserted = db.execute(
        postgresql_insert(ConsumedRefreshToken)
        .values(
            token_hash=token_hash,
            user_id=user_id,
            expires_at=expires_at,
            attempt_id=attempt_id,
        )
        .on_conflict_do_nothing(index_elements=[ConsumedRefreshToken.token_hash])
        .returning(ConsumedRefreshToken.token_hash)
    ).scalar_one_or_none()
    if inserted is None:
        existing = db.execute(
            select(ConsumedRefreshToken).where(ConsumedRefreshToken.token_hash == token_hash)
        ).scalar_one_or_none()
        if existing is None:
            return None
        return _replayed_tokens(existing, attempt_id=attempt_id, current_time=current_time)

    rotated = RotatedRefreshTokens(
        access_token=create_access_token(user_id),
        refresh_token=create_refresh_token(user_id),
        replayed=False,
    )
    if attempt_id is not None:
        replay_expires_at = min(expires_at, current_time + REFRESH_REPLAY_TTL)
        db.execute(
            update(ConsumedRefreshToken)
            .where(ConsumedRefreshToken.token_hash == token_hash)
            .values(
                replay_payload_encrypted=_encrypt_replay_payload(rotated),
                replay_expires_at=replay_expires_at,
            )
        )
    return rotated


def _replayed_tokens(
    existing: ConsumedRefreshToken,
    *,
    attempt_id: str | None,
    current_time: datetime,
) -> RotatedRefreshTokens | None:
    if (
        attempt_id is None
        or existing.attempt_id != attempt_id
        or existing.replay_payload_encrypted is None
        or existing.replay_expires_at is None
        or existing.replay_expires_at <= current_time
    ):
        return None
    return _decrypt_replay_payload(existing.replay_payload_encrypted)


def _encrypt_replay_payload(tokens: RotatedRefreshTokens) -> str:
    payload = json.dumps(
        {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return _replay_cipher().encrypt(payload.encode("utf-8")).decode("utf-8")


def _decrypt_replay_payload(encrypted_payload: str) -> RotatedRefreshTokens:
    try:
        raw_payload = _replay_cipher().decrypt(encrypted_payload.encode("utf-8"))
        parsed = json.loads(raw_payload.decode("utf-8"))
        access_token = parsed["access_token"]
        refresh_token = parsed["refresh_token"]
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Replay access token is invalid")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise ValueError("Replay refresh token is invalid")
    except (
        InvalidToken,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise RefreshTokenReplayError("Stored refresh replay payload is invalid") from exc
    return RotatedRefreshTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        replayed=True,
    )


def _replay_cipher() -> Fernet:
    """Derive a purpose-separated replay key from the required auth signing secret."""
    secret = get_settings().JWT_SECRET_KEY.encode("utf-8")
    derived = hashlib.sha256(_REPLAY_KEY_CONTEXT + secret).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _expiration_datetime(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Refresh token is missing a valid expiry")
    return datetime.fromtimestamp(float(value), tz=UTC)
