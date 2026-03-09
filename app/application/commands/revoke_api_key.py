"""Application command for admin API key revocation."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repositories.api_key_repository import revoke_api_key


def execute(db: Session, *, api_key_id: int):
    """Revoke an API key."""
    record = revoke_api_key(db, api_key_id=api_key_id)
    if record is None:
        raise HTTPException(status_code=404, detail="API key not found")
    return record
