"""Application query for user-managed LLM integrations."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.api.integrations import UserLlmIntegrationResponse
from app.models.contracts import UserLlmProvider
from app.repositories.user_integration_repository import (
    SUPPORTED_LLM_PROVIDERS,
    list_user_llm_integrations,
)


def execute(db: Session, *, user_id: int) -> list[UserLlmIntegrationResponse]:
    """List user-managed LLM provider key summaries."""
    responses: list[UserLlmIntegrationResponse] = []
    for record in list_user_llm_integrations(db, user_id=user_id):
        provider = record.provider
        if provider not in SUPPORTED_LLM_PROVIDERS:
            continue
        responses.append(
            UserLlmIntegrationResponse(
                provider=UserLlmProvider(provider),
                configured=bool(record.access_token_encrypted),
                updated_at=record.updated_at,
            )
        )
    return responses
