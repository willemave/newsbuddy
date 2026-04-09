"""Service functions for recording user content interaction analytics."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.schema import AnalyticsInteraction, Content

logger = get_logger(__name__)

INTERACTION_TYPE_OPENED = "opened"


@dataclass(frozen=True)
class RecordContentInteractionInput:
    """Input payload for recording a content interaction."""

    user_id: int
    content_id: int
    interaction_id: str
    interaction_type: str
    occurred_at: datetime | None = None
    surface: str | None = None
    context_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordContentInteractionResult:
    """Result payload from interaction recording."""

    recorded: bool
    interaction_id: str
    analytics_interaction_id: int | None


class ContentInteractionContentNotFoundError(ValueError):
    """Raised when an interaction references a non-existent content row."""


def _normalize_timestamp(timestamp: datetime | None) -> datetime:
    if timestamp is None:
        return datetime.now(UTC).replace(tzinfo=None)
    if timestamp.tzinfo is None:
        return timestamp
    return timestamp.astimezone(UTC).replace(tzinfo=None)


def _interaction_extra(operation: str, **context_data: Any) -> dict[str, Any]:
    return {
        "component": "content_interactions",
        "operation": operation,
        "item_id": str(context_data.get("content_id"))
        if context_data.get("content_id") is not None
        else None,
        "context_data": {key: value for key, value in context_data.items() if value is not None},
    }


def record_content_interaction(
    db: Session,
    payload: RecordContentInteractionInput,
) -> RecordContentInteractionResult:
    """Record a user-content interaction with idempotency semantics."""
    logger.info(
        "[CONTENT_INTERACTIONS] Recording interaction",
        extra=_interaction_extra(
            "record_content_interaction",
            user_id=payload.user_id,
            content_id=payload.content_id,
            interaction_type=payload.interaction_type,
            interaction_id=payload.interaction_id,
        ),
    )

    existing_content_id = db.execute(
        select(Content.id).where(Content.id == payload.content_id)
    ).scalar_one_or_none()
    if existing_content_id is None:
        logger.warning(
            "[CONTENT_INTERACTIONS] Content not found",
            extra=_interaction_extra(
                "record_content_interaction",
                user_id=payload.user_id,
                content_id=payload.content_id,
                interaction_type=payload.interaction_type,
                interaction_id=payload.interaction_id,
            ),
        )
        raise ContentInteractionContentNotFoundError(
            f"Content not found for content_id={payload.content_id}"
        )

    try:
        existing = db.execute(
            select(AnalyticsInteraction).where(
                AnalyticsInteraction.user_id == payload.user_id,
                AnalyticsInteraction.interaction_id == payload.interaction_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return RecordContentInteractionResult(
                recorded=False,
                interaction_id=payload.interaction_id,
                analytics_interaction_id=existing.id,
            )

        interaction = AnalyticsInteraction(
            user_id=payload.user_id,
            content_id=payload.content_id,
            interaction_type=payload.interaction_type,
            interaction_id=payload.interaction_id,
            surface=payload.surface,
            context_data=payload.context_data or {},
            occurred_at=_normalize_timestamp(payload.occurred_at),
        )
        db.add(interaction)
        db.flush()
        analytics_interaction_id = int(interaction.id)
        db.commit()
        return RecordContentInteractionResult(
            recorded=True,
            interaction_id=payload.interaction_id,
            analytics_interaction_id=analytics_interaction_id,
        )
    except IntegrityError as exc:
        db.rollback()
        existing = db.execute(
            select(AnalyticsInteraction).where(
                AnalyticsInteraction.user_id == payload.user_id,
                AnalyticsInteraction.interaction_id == payload.interaction_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return RecordContentInteractionResult(
                recorded=False,
                interaction_id=payload.interaction_id,
                analytics_interaction_id=existing.id,
            )

        logger.error(
            "[CONTENT_INTERACTIONS] Integrity error while recording interaction",
            extra=_interaction_extra(
                "record_content_interaction",
                user_id=payload.user_id,
                content_id=payload.content_id,
                interaction_type=payload.interaction_type,
                interaction_id=payload.interaction_id,
                error=str(exc),
            ),
        )
        raise
    except OperationalError as exc:
        db.rollback()
        logger.error(
            "[CONTENT_INTERACTIONS] Database write failed while recording interaction",
            extra=_interaction_extra(
                "record_content_interaction",
                user_id=payload.user_id,
                content_id=payload.content_id,
                interaction_type=payload.interaction_type,
                interaction_id=payload.interaction_id,
                error=str(exc),
            ),
        )
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception(
            "[CONTENT_INTERACTIONS] Unexpected error while recording interaction",
            extra=_interaction_extra(
                "record_content_interaction",
                user_id=payload.user_id,
                content_id=payload.content_id,
                interaction_type=payload.interaction_type,
                interaction_id=payload.interaction_id,
                error=str(exc),
            ),
        )
        raise
