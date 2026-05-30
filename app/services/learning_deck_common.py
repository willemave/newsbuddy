"""Shared types and small helpers for Learning Deck services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.models.contracts import LearningDeckRunStatus, LearningDeckSourceKind
from app.models.db import User
from app.models.db.learning_deck import ACTIVE_LEARNING_DECK_RUN_STATUS_VALUES

ACTIVE_RUN_STATUSES = set(ACTIVE_LEARNING_DECK_RUN_STATUS_VALUES)


class LearningDeckError(ValueError):
    """Raised for user-correctable Learning Deck failures."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class LearningDeckSourceNotReady(RuntimeError):
    """Raised when content ingestion/extraction must finish before deck generation."""


@dataclass(frozen=True)
class LearningDeckSource:
    """Normalized source target for a Learning Deck."""

    source_kind: LearningDeckSourceKind
    source_identity: str
    source_url: str | None
    source_content_id: int | None
    source_title: str
    source_metadata: dict[str, Any]


@dataclass(frozen=True)
class LearningDeckSignedToken:
    """Decoded private signed viewer token."""

    deck_id: int
    user_id: int


@dataclass(frozen=True)
class LearningDeckHostedObject:
    """One hosted Learning Deck object plus response content type."""

    data: bytes
    media_type: str


def append_learning_deck_timeline(
    run: Any,
    *,
    status: str | LearningDeckRunStatus,
    note: str,
) -> None:
    """Append one coarse user-visible timeline note."""
    status_value = status.value if isinstance(status, LearningDeckRunStatus) else status
    entries = list(run.timeline or [])
    entries.append(
        {
            "status": status_value,
            "note": note,
            "created_at": utcnow().isoformat(),
        }
    )
    run.timeline = entries


def coerce_string_list(value: Any) -> list[str]:
    """Return string list values from a JSON-ish field."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def clean_optional_text(value: str | None) -> str | None:
    """Trim an optional string and normalize blank values to None."""
    cleaned = value.strip() if value else ""
    return cleaned or None


def require_user_id(user: User) -> int:
    """Return a persisted user id or raise a Learning Deck API error."""
    if user.id is None:
        raise LearningDeckError("User is missing an id", status_code=401)
    return int(user.id)


def require_int_value(value: int | None, label: str, *, status_code: int = 500) -> int:
    """Return a persisted integer value or fail with a service-layer error."""
    if value is None:
        raise LearningDeckError(f"{label} is missing", status_code=status_code)
    return int(value)


def require_str_value(value: str | None, label: str, *, status_code: int = 500) -> str:
    """Return a persisted string value or fail with a service-layer error."""
    if value is None:
        raise LearningDeckError(f"{label} is missing", status_code=status_code)
    return value


def require_datetime_value(
    value: datetime | None,
    label: str,
    *,
    status_code: int = 500,
) -> datetime:
    """Return a persisted datetime value or fail with a service-layer error."""
    if value is None:
        raise LearningDeckError(f"{label} is missing", status_code=status_code)
    return value


def utcnow() -> datetime:
    """Return the repo's normalized naive UTC timestamp shape."""
    return datetime.now(UTC).replace(tzinfo=None)
