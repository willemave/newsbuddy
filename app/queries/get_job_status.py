"""Application query for async job status."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.common import JobStatusResponse
from app.models.schema import ProcessingTask


def execute(db: Session, *, job_id: int) -> JobStatusResponse:
    """Return job status for a processing task."""
    task = db.query(ProcessingTask).filter(ProcessingTask.id == job_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(
        id=task.id,
        task_type=task.task_type,
        status=task.status,
        queue_name=task.queue_name,
        content_id=task.content_id,
        payload=task.payload or {},
        retry_count=task.retry_count or 0,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        error_message=task.error_message,
    )
