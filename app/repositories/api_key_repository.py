"""Persistence helpers for user API keys."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.infrastructure.security.api_keys import extract_key_prefix, generate_api_key
from app.infrastructure.security.key_hashing import hash_api_key, verify_api_key_hash
from app.models.schema import UserApiKey


def create_api_key(
    db: Session,
    *,
    user_id: int,
    created_by_admin_user_id: int | None,
) -> tuple[UserApiKey, str]:
    """Create a new API key record and return the raw key once."""
    generated = generate_api_key()
    record = UserApiKey(
        user_id=user_id,
        key_prefix=generated.key_prefix,
        key_hash=hash_api_key(generated.raw_key),
        created_by_admin_user_id=created_by_admin_user_id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record, generated.raw_key


def list_api_keys(db: Session, *, user_id: int | None = None) -> list[UserApiKey]:
    """List API keys, optionally filtered by user."""
    query = db.query(UserApiKey).order_by(UserApiKey.created_at.desc(), UserApiKey.id.desc())
    if user_id is not None:
        query = query.filter(UserApiKey.user_id == user_id)
    return list(query.all())


def revoke_api_key(db: Session, *, api_key_id: int) -> UserApiKey | None:
    """Revoke an API key if it exists."""
    record = db.query(UserApiKey).filter(UserApiKey.id == api_key_id).first()
    if record is None:
        return None
    if record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
        db.commit()
        db.refresh(record)
    return record


def find_active_api_key_by_token(db: Session, *, raw_key: str) -> UserApiKey | None:
    """Find an active API key record matching a raw token."""
    prefix = extract_key_prefix(raw_key)
    candidates = (
        db.query(UserApiKey)
        .filter(UserApiKey.key_prefix == prefix)
        .filter(UserApiKey.revoked_at.is_(None))
        .all()
    )
    for candidate in candidates:
        if verify_api_key_hash(raw_key, candidate.key_hash):
            return candidate
    return None


def touch_last_used(db: Session, *, api_key_id: int) -> None:
    """Update last-used timestamp for an API key."""
    record = db.query(UserApiKey).filter(UserApiKey.id == api_key_id).first()
    if record is None:
        return
    record.last_used_at = datetime.now(UTC)
    db.commit()
