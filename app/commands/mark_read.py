"""Application commands for read-status updates."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.db import temporary_sqlite_busy_timeout
from app.models.schema import Content, ContentReadStatus
from app.repositories import read_status_repository


def mark_read(db: Session, *, user_id: int, content_id: int) -> dict[str, object]:
    """Mark a content item as read."""
    with temporary_sqlite_busy_timeout(db, read_status_repository.READ_STATUS_BUSY_TIMEOUT_MS):
        content = db.query(Content).filter(Content.id == content_id).first()
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    result = read_status_repository.mark_content_as_read(db, content_id, user_id)
    if result is None:
        return {"status": "error", "message": "Failed to mark as read"}
    return {"status": "success", "content_id": content_id}


def mark_unread(db: Session, *, user_id: int, content_id: int) -> dict[str, object]:
    """Mark a content item as unread."""
    content = db.query(Content).filter(Content.id == content_id).first()
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    result = db.execute(
        delete(ContentReadStatus).where(
            ContentReadStatus.content_id == content_id,
            ContentReadStatus.user_id == user_id,
        )
    )
    db.commit()
    return {
        "status": "success",
        "content_id": content_id,
        "removed_records": result.rowcount,
    }


def bulk_mark_read(db: Session, *, user_id: int, content_ids: list[int]) -> dict[str, object]:
    """Bulk mark content items as read."""
    existing_ids = db.query(Content.id).filter(Content.id.in_(content_ids)).all()
    existing_id_set = {row[0] for row in existing_ids}
    invalid_ids = set(content_ids) - existing_id_set
    if invalid_ids:
        raise HTTPException(status_code=400, detail=f"Invalid content IDs: {sorted(invalid_ids)}")

    success_count, failed_ids = read_status_repository.mark_contents_as_read(
        db,
        content_ids,
        user_id,
    )
    return {
        "status": "success",
        "marked_count": success_count,
        "failed_ids": failed_ids,
        "total_requested": len(content_ids),
    }
