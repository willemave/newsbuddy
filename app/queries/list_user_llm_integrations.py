"""Application query for user-managed LLM integrations."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.api.common import UserLlmIntegrationResponse
from app.repositories.user_integration_repository import list_user_llm_integrations


def execute(db: Session, *, user_id: int) -> list[UserLlmIntegrationResponse]:
    """List user-managed LLM provider key summaries."""
    return [
        UserLlmIntegrationResponse(
            provider=record.provider,
            configured=bool(record.access_token_encrypted),
            updated_at=record.updated_at,
        )
        for record in list_user_llm_integrations(db, user_id=user_id)
    ]
