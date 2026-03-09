"""Application query for admin API key listings."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.repositories.api_key_repository import list_api_keys as list_api_key_records
from app.routers.api.models import ApiKeySummaryResponse


def execute(db: Session, *, user_id: int | None = None) -> list[ApiKeySummaryResponse]:
    """List API key summaries."""
    return [
        ApiKeySummaryResponse(
            id=record.id,
            user_id=record.user_id,
            key_prefix=record.key_prefix,
            created_at=record.created_at,
            revoked_at=record.revoked_at,
            last_used_at=record.last_used_at,
            created_by_admin_user_id=record.created_by_admin_user_id,
        )
        for record in list_api_key_records(db, user_id=user_id)
    ]
