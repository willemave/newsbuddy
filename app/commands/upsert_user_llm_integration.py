"""Application command for storing a user-managed LLM provider key."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.common import UserLlmIntegrationResponse
from app.repositories.user_integration_repository import (
    SUPPORTED_LLM_PROVIDERS,
    upsert_user_llm_integration,
)


def execute(
    db: Session,
    *,
    user_id: int,
    provider: str,
    api_key: str,
) -> UserLlmIntegrationResponse:
    """Store/update a user-managed LLM provider key."""
    if provider not in SUPPORTED_LLM_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unsupported provider")
    record = upsert_user_llm_integration(db, user_id=user_id, provider=provider, api_key=api_key)
    return UserLlmIntegrationResponse(
        provider=record.provider,
        configured=bool(record.access_token_encrypted),
        updated_at=record.updated_at,
    )
