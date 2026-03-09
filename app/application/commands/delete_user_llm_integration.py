"""Application command for deleting a user-managed LLM provider key."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repositories.user_integration_repository import (
    SUPPORTED_LLM_PROVIDERS,
    delete_user_llm_integration,
)


def execute(db: Session, *, user_id: int, provider: str) -> dict[str, str]:
    """Delete a user-managed LLM provider key."""
    if provider not in SUPPORTED_LLM_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unsupported provider")
    deleted = delete_user_llm_integration(db, user_id=user_id, provider=provider)
    if not deleted:
        raise HTTPException(status_code=404, detail="Integration not found")
    return {"status": "deleted", "provider": provider}
