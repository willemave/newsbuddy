"""Application command for admin API key creation."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.api.common import ApiKeyCreateResponse, ApiKeySummaryResponse
from app.repositories.api_key_repository import create_api_key


def execute(
    db: Session,
    *,
    user_id: int,
    created_by_admin_user_id: int | None,
) -> ApiKeyCreateResponse:
    """Create an API key and return the raw secret once."""
    record, raw_key = create_api_key(
        db,
        user_id=user_id,
        created_by_admin_user_id=created_by_admin_user_id,
    )
    summary = ApiKeySummaryResponse(
        id=record.id,
        user_id=record.user_id,
        key_prefix=record.key_prefix,
        created_at=record.created_at,
        revoked_at=record.revoked_at,
        last_used_at=record.last_used_at,
        created_by_admin_user_id=record.created_by_admin_user_id,
    )
    return ApiKeyCreateResponse(
        api_key=raw_key,
        key=raw_key,
        key_prefix=record.key_prefix,
        record=summary,
    )
