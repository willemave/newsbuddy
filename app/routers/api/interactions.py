"""Interaction analytics endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db_session
from app.core.deps import get_current_user, require_user_id
from app.models.api.common import (
    RecordContentInteractionRequest,
    RecordContentInteractionResponse,
)
from app.models.user import User
from app.services.content_interactions import (
    ContentInteractionContentNotFoundError,
    RecordContentInteractionInput,
    record_content_interaction,
)

router = APIRouter()


@router.post(
    "/analytics",
    response_model=RecordContentInteractionResponse,
    summary="Record content interaction",
    description="Record a user-content interaction for analytics with idempotency support.",
    responses={
        200: {"description": "Interaction recorded"},
        404: {"description": "Content not found"},
        401: {"description": "Authentication required"},
    },
)
async def post_content_interaction(
    request: RecordContentInteractionRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RecordContentInteractionResponse:
    """Record a user interaction for a content item."""
    user_id = require_user_id(current_user)
    try:
        result = record_content_interaction(
            db,
            RecordContentInteractionInput(
                user_id=user_id,
                content_id=request.content_id,
                interaction_id=request.interaction_id,
                interaction_type=request.interaction_type,
                occurred_at=request.occurred_at,
                surface=request.surface,
                context_data=request.context_data,
            ),
        )
    except ContentInteractionContentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Content not found") from exc

    return RecordContentInteractionResponse(
        status="success",
        recorded=result.recorded,
        interaction_id=result.interaction_id,
        analytics_interaction_id=result.analytics_interaction_id,
    )
