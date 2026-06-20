"""VM-backed ShareSheet action API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user, require_user_id
from app.models.api.share_actions import ShareActionCreateRequest, ShareActionResponse
from app.models.db import User
from app.services.llm_tasks import LlmTaskError, get_llm_task
from app.services.share_actions import create_share_action, present_share_action

router = APIRouter(prefix="/share-actions", tags=["share-actions"])


@router.post(
    "",
    response_model=ShareActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a VM-backed ShareSheet action",
)
def create_action(
    payload: ShareActionCreateRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ShareActionResponse:
    """Create and enqueue a VM-backed ShareSheet action workflow."""
    try:
        return create_share_action(db, current_user=current_user, payload=payload)
    except LlmTaskError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/{task_id}",
    response_model=ShareActionResponse,
    summary="Get one ShareSheet action task",
)
def get_action(
    task_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ShareActionResponse:
    """Return one Share Action task for the current user."""
    task = get_llm_task(db, user_id=require_user_id(current_user), llm_task_id=task_id)
    if task is None or task.task_kind != "share_action":
        raise HTTPException(status_code=404, detail="Share Action not found")
    return present_share_action(db, task)
