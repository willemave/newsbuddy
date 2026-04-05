"""Application command for agent digest generation."""

from __future__ import annotations

from datetime import UTC

from sqlalchemy.orm import Session

from app.models.api.common import AgentDigestRequest, AgentDigestResponse
from app.models.contracts import TaskType
from app.services.gateways.task_queue_gateway import get_task_queue_gateway


def execute(
    db: Session,
    *,
    user_id: int,
    payload: AgentDigestRequest,
) -> AgentDigestResponse:
    """Queue an agent digest generation task."""
    del db
    queue = get_task_queue_gateway()
    job_id = queue.enqueue(
        TaskType.GENERATE_NEWS_DIGEST,
        payload={
            "user_id": user_id,
            "local_date": payload.end_at.astimezone(UTC).date().isoformat(),
            "timezone": "UTC",
            "force_regenerate": True,
            "start_at": payload.start_at.isoformat(),
            "end_at": payload.end_at.isoformat(),
            "form": payload.form,
        },
    )
    return AgentDigestResponse(job_id=job_id)
