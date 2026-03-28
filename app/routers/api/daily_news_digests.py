"""Daily news digest list and read-status endpoints."""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user
from app.models.pagination import PaginationMetadata
from app.models.schema import DailyNewsDigest
from app.models.user import User
from app.routers.api.chat_models import (
    ChatMessageDto,
    ChatMessageRole,
    ChatSessionSummaryDto,
    StartDailyDigestChatResponse,
)
from app.routers.api.chat_models import (
    MessageProcessingStatus as MessageProcessingStatusDto,
)
from app.routers.api.models import (
    DailyNewsDigestBulletDetailResponse,
    DailyNewsDigestCitationResponse,
    DailyNewsDigestListResponse,
    DailyNewsDigestResponse,
)
from app.services.chat_agent import process_message_async
from app.services.daily_digest_chat import start_daily_digest_bullet_chat, start_daily_digest_chat
from app.services.daily_news_digest import (
    MAX_DAILY_DIGEST_BULLETS,
    DailyDigestSourceItem,
    load_daily_digest_source_items,
    resolve_daily_digest_bullet_details,
)
from app.services.event_logger import log_event

router = APIRouter()


def _isoformat_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    value = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return value.isoformat().replace("+00:00", "Z")


def _encode_cursor(*, last_id: int, last_local_date: date, read_filter: str) -> str:
    payload = {
        "last_id": last_id,
        "last_local_date": last_local_date.isoformat(),
        "read_filter": read_filter,
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def _decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("utf-8"))
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Invalid digest cursor") from exc

    if not isinstance(payload, dict):
        raise ValueError("Invalid digest cursor")

    if not isinstance(payload.get("last_id"), int):
        raise ValueError("Invalid digest cursor")
    if not isinstance(payload.get("last_local_date"), str):
        raise ValueError("Invalid digest cursor")
    if payload.get("read_filter") not in {"all", "read", "unread"}:
        raise ValueError("Invalid digest cursor")

    try:
        payload["last_local_date"] = date.fromisoformat(payload["last_local_date"])
    except ValueError as exc:
        raise ValueError("Invalid digest cursor") from exc

    return payload


def _build_digest_response(
    digest: DailyNewsDigest,
    *,
    source_items_by_content_id: dict[int, DailyDigestSourceItem] | None = None,
) -> DailyNewsDigestResponse:
    key_points = digest.key_points if isinstance(digest.key_points, list) else []
    source_ids = digest.source_content_ids if isinstance(digest.source_content_ids, list) else []
    normalized_source_ids = [int(cid) for cid in source_ids if isinstance(cid, int)]
    digest_source_items = source_items_by_content_id or {}
    bullet_details = resolve_daily_digest_bullet_details(
        digest,
        source_items_by_content_id=digest_source_items,
    )
    bullet_detail_responses = [
        DailyNewsDigestBulletDetailResponse(
            text=bullet.text,
            source_count=len(
                [
                    citation_id
                    for citation_id in bullet.source_content_ids
                    if citation_id in digest_source_items
                ]
            ),
            citations=[
                DailyNewsDigestCitationResponse(
                    content_id=citation_id,
                    label=digest_source_items[citation_id].source_label,
                    title=digest_source_items[citation_id].title,
                    url=digest_source_items[citation_id].source_url,
                )
                for citation_id in bullet.source_content_ids
                if citation_id in digest_source_items
            ],
            comment_quotes=bullet.comment_quotes,
        )
        for bullet in bullet_details
    ]
    source_labels = list(
        dict.fromkeys(
            [
                source_item.source_label.strip()
                for content_id in normalized_source_ids
                for source_item in [digest_source_items.get(content_id)]
                if source_item is not None
                and isinstance(source_item.source_label, str)
                and source_item.source_label.strip()
            ]
        )
    )
    resolved_key_points = [point for point in key_points if isinstance(point, str)]
    if not resolved_key_points:
        resolved_key_points = [bullet.text for bullet in bullet_details]
    return DailyNewsDigestResponse(
        id=digest.id,
        local_date=digest.local_date.isoformat(),
        timezone=digest.timezone,
        title=digest.title,
        summary=digest.summary,
        key_points=resolved_key_points,
        bullet_details=bullet_detail_responses,
        source_count=int(digest.source_count or 0),
        source_content_ids=normalized_source_ids,
        source_labels=source_labels,
        is_read=digest.read_at is not None,
        read_at=_isoformat_utc(digest.read_at),
        generated_at=_isoformat_utc(digest.generated_at) or "",
        coverage_end_at=_isoformat_utc(digest.coverage_end_at),
    )


def _get_user_digest_or_404(
    *,
    db: Session,
    user_id: int,
    digest_id: int,
) -> DailyNewsDigest:
    digest = (
        db.query(DailyNewsDigest)
        .filter(DailyNewsDigest.id == digest_id, DailyNewsDigest.user_id == user_id)
        .first()
    )
    if digest is None:
        raise HTTPException(status_code=404, detail="Daily digest not found")
    return digest


def _get_digest_source_items(
    db: Session,
    digest: DailyNewsDigest,
) -> dict[int, DailyDigestSourceItem]:
    """Load normalized source items scoped to one digest row."""
    source_ids = digest.source_content_ids if isinstance(digest.source_content_ids, list) else []
    return load_daily_digest_source_items(
        db,
        content_ids=[int(content_id) for content_id in source_ids if isinstance(content_id, int)],
    )


def _build_digest_narration_text(digest: DailyNewsDigest) -> str:
    points = digest.key_points if isinstance(digest.key_points, list) else []
    cleaned_points = [point.strip() for point in points if isinstance(point, str) and point.strip()]

    narration_parts: list[str] = []
    if cleaned_points:
        narration_parts.append("Key points:")
        narration_parts.extend(cleaned_points[:MAX_DAILY_DIGEST_BULLETS])
    elif digest.summary.strip():
        narration_parts.append(digest.summary.strip())

    return " ".join(part for part in narration_parts if part)


@router.get(
    "/daily-digests",
    response_model=DailyNewsDigestListResponse,
    summary="List daily news digest cards",
)
def list_daily_news_digests(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    read_filter: Annotated[
        str,
        Query(
            description="Filter by read status (all/read/unread)",
            pattern="^(all|read|unread)$",
        ),
    ] = "unread",
    cursor: Annotated[str | None, Query(description="Pagination cursor for next page")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> DailyNewsDigestListResponse:
    """List per-user daily digest rows."""
    last_id: int | None = None
    last_local_date: date | None = None
    if cursor:
        try:
            decoded = _decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if decoded["read_filter"] != read_filter:
            raise HTTPException(status_code=400, detail="Cursor invalid: filters changed.")
        last_id = decoded["last_id"]
        last_local_date = decoded["last_local_date"]

    query = db.query(DailyNewsDigest).filter(DailyNewsDigest.user_id == current_user.id)
    if read_filter == "read":
        query = query.filter(DailyNewsDigest.read_at.is_not(None))
    elif read_filter == "unread":
        query = query.filter(DailyNewsDigest.read_at.is_(None))

    if last_id is not None and last_local_date is not None:
        query = query.filter(
            or_(
                DailyNewsDigest.local_date < last_local_date,
                and_(DailyNewsDigest.local_date == last_local_date, DailyNewsDigest.id < last_id),
            )
        )

    rows = (
        query.order_by(DailyNewsDigest.local_date.desc(), DailyNewsDigest.id.desc())
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last_row = rows[-1]
        next_cursor = _encode_cursor(
            last_id=last_row.id,
            last_local_date=last_row.local_date,
            read_filter=read_filter,
        )

    all_source_ids = {
        int(content_id)
        for row in rows
        for content_id in (
            row.source_content_ids if isinstance(row.source_content_ids, list) else []
        )
        if isinstance(content_id, int)
    }
    source_items_by_content_id = load_daily_digest_source_items(
        db,
        content_ids=sorted(all_source_ids),
    )
    digest_responses = [
        _build_digest_response(
            row,
            source_items_by_content_id={
                content_id: source_items_by_content_id[content_id]
                for content_id in (
                    row.source_content_ids if isinstance(row.source_content_ids, list) else []
                )
                if isinstance(content_id, int) and content_id in source_items_by_content_id
            },
        )
        for row in rows
    ]

    return DailyNewsDigestListResponse(
        digests=digest_responses,
        meta=PaginationMetadata(
            next_cursor=next_cursor,
            has_more=has_more,
            page_size=len(rows),
            total=len(rows),
        ),
    )


@router.post(
    "/daily-digests/{digest_id}/mark-read",
    summary="Mark one daily digest as read",
)
def mark_daily_digest_read(
    digest_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Mark a single daily digest row as read."""
    digest = _get_user_digest_or_404(db=db, user_id=current_user.id, digest_id=digest_id)
    digest.read_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    return {
        "status": "success",
        "digest_id": digest.id,
        "is_read": True,
        "read_at": _isoformat_utc(digest.read_at),
    }


@router.delete(
    "/daily-digests/{digest_id}/mark-unread",
    summary="Mark one daily digest as unread",
)
def mark_daily_digest_unread(
    digest_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Mark a single daily digest row as unread."""
    digest = _get_user_digest_or_404(db=db, user_id=current_user.id, digest_id=digest_id)
    digest.read_at = None
    db.commit()
    return {
        "status": "success",
        "digest_id": digest.id,
        "is_read": False,
        "read_at": None,
    }


@router.post(
    "/daily-digests/{digest_id}/bullets/{bullet_index}/dig-deeper",
    response_model=StartDailyDigestChatResponse,
    summary="Start a bullet-focused daily digest dig-deeper chat",
)
async def start_daily_digest_bullet_dig_deeper(
    digest_id: Annotated[int, Path(..., gt=0)],
    bullet_index: Annotated[int, Path(..., ge=0)],
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> StartDailyDigestChatResponse:
    """Create a fresh daily-digest chat session focused on one selected bullet."""
    digest = _get_user_digest_or_404(db=db, user_id=current_user.id, digest_id=digest_id)
    source_items_by_content_id = _get_digest_source_items(db, digest)

    try:
        session, db_message, prompt = start_daily_digest_bullet_chat(
            db,
            digest=digest,
            bullet_index=bullet_index,
            user_id=current_user.id,
            source_items_by_content_id=source_items_by_content_id,
        )
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="Daily digest bullet not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    background_tasks.add_task(process_message_async, session.id, db_message.id, prompt)

    log_event(
        event_type="chat",
        event_name="daily_digest_bullet_chat_started",
        status="started",
        user_id=current_user.id,
        session_id=session.id,
        digest_id=digest.id,
        model=session.llm_model,
    )

    return StartDailyDigestChatResponse(
        session=ChatSessionSummaryDto(
            id=session.id,
            title=session.title,
            content_id=session.content_id,
            session_type=session.session_type,
            topic=session.topic,
            llm_model=session.llm_model,
            llm_provider=session.llm_provider,
            created_at=session.created_at,
            updated_at=session.updated_at,
            last_message_at=session.last_message_at,
            is_archived=session.is_archived,
            article_title=None,
            article_url=None,
            article_summary=None,
            article_source=None,
            has_pending_message=True,
            is_favorite=False,
            has_messages=True,
            last_message_preview=None,
            last_message_role=None,
        ),
        user_message=ChatMessageDto(
            id=db_message.id,
            session_id=session.id,
            role=ChatMessageRole.USER,
            content=prompt,
            timestamp=db_message.created_at,
            status=MessageProcessingStatusDto.PROCESSING,
        ),
        message_id=db_message.id,
        status=MessageProcessingStatusDto.PROCESSING,
    )


@router.post(
    "/daily-digests/{digest_id}/dig-deeper",
    response_model=StartDailyDigestChatResponse,
    summary="Start a daily digest dig-deeper chat",
)
async def start_daily_digest_dig_deeper(
    digest_id: Annotated[int, Path(..., gt=0)],
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> StartDailyDigestChatResponse:
    """Create a fresh daily-digest chat seeded from digest bullets only."""
    digest = _get_user_digest_or_404(db=db, user_id=current_user.id, digest_id=digest_id)

    try:
        session, db_message, prompt = start_daily_digest_chat(
            db,
            digest=digest,
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    background_tasks.add_task(process_message_async, session.id, db_message.id, prompt)

    log_event(
        event_type="chat",
        event_name="daily_digest_chat_started",
        status="started",
        user_id=current_user.id,
        session_id=session.id,
        digest_id=digest.id,
        model=session.llm_model,
    )

    return StartDailyDigestChatResponse(
        session=ChatSessionSummaryDto(
            id=session.id,
            title=session.title,
            content_id=session.content_id,
            session_type=session.session_type,
            topic=session.topic,
            llm_model=session.llm_model,
            llm_provider=session.llm_provider,
            created_at=session.created_at,
            updated_at=session.updated_at,
            last_message_at=session.last_message_at,
            is_archived=session.is_archived,
            article_title=None,
            article_url=None,
            article_summary=None,
            article_source=None,
            has_pending_message=True,
            is_favorite=False,
            has_messages=True,
            last_message_preview=None,
            last_message_role=None,
        ),
        user_message=ChatMessageDto(
            id=db_message.id,
            session_id=session.id,
            role=ChatMessageRole.USER,
            content=prompt,
            timestamp=db_message.created_at,
            status=MessageProcessingStatusDto.PROCESSING,
        ),
        message_id=db_message.id,
        status=MessageProcessingStatusDto.PROCESSING,
    )
