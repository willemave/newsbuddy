"""LLM task workflow action API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user, require_user_id
from app.models.api.llm_tasks import (
    LlmTaskActionListResponse,
    LlmTaskActionRejectRequest,
    LlmTaskActionResponse,
)
from app.models.db import User
from app.services.llm_tasks import (
    LlmTaskError,
    approve_llm_task_action,
    get_llm_task,
    get_llm_task_action,
    list_llm_task_actions,
    present_llm_task_action,
    reject_llm_task_action,
)

router = APIRouter(prefix="/llm-tasks", tags=["llm-tasks"])


@router.get(
    "/{task_id}/actions",
    response_model=LlmTaskActionListResponse,
    summary="List actions for one LLM task",
)
def list_task_actions(
    task_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LlmTaskActionListResponse:
    """Return user-visible action events for one owned LLM task."""
    task = get_llm_task(db, user_id=require_user_id(current_user), llm_task_id=task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="LLM task not found")
    return LlmTaskActionListResponse(
        actions=[present_llm_task_action(action) for action in list_llm_task_actions(db, task=task)]
    )


@router.post(
    "/{task_id}/actions/{action_id}/approve",
    response_model=LlmTaskActionResponse,
    summary="Approve an LLM task action",
)
def approve_task_action(
    task_id: Annotated[int, Path(..., gt=0)],
    action_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LlmTaskActionResponse:
    """Approve an action that is waiting for user confirmation."""
    user_id = require_user_id(current_user)
    task = get_llm_task(db, user_id=user_id, llm_task_id=task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="LLM task not found")
    action = get_llm_task_action(db, task=task, action_id=action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="LLM task action not found")
    try:
        approve_llm_task_action(db, action=action, approved_by_user_id=user_id)
    except LlmTaskError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if task.task_kind == "share_action":
        from app.services.share_actions import apply_share_task_action

        try:
            apply_share_task_action(db, task=task, action=action)
        except Exception as exc:  # noqa: BLE001
            db.commit()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(action)
    return present_llm_task_action(action)


@router.post(
    "/{task_id}/actions/{action_id}/reject",
    response_model=LlmTaskActionResponse,
    summary="Reject an LLM task action",
)
def reject_task_action(
    task_id: Annotated[int, Path(..., gt=0)],
    action_id: Annotated[int, Path(..., gt=0)],
    payload: LlmTaskActionRejectRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LlmTaskActionResponse:
    """Reject an action that is waiting for user confirmation."""
    user_id = require_user_id(current_user)
    task = get_llm_task(db, user_id=user_id, llm_task_id=task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="LLM task not found")
    action = get_llm_task_action(db, task=task, action_id=action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="LLM task action not found")
    try:
        reject_llm_task_action(db, action=action, error_message=payload.reason)
    except LlmTaskError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(action)
    return present_llm_task_action(action)
