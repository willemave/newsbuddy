"""Tests for shared pagination cursor utilities."""

import base64
import json
from datetime import UTC, datetime

import pytest

from app.utils.pagination import PaginationCursor


def _encode_payload(payload: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()


def test_decode_cursor_rejects_missing_last_id() -> None:
    cursor = _encode_payload({"last_created_at": datetime.now(UTC).isoformat()})

    with pytest.raises(ValueError, match="last_id"):
        PaginationCursor.decode_cursor(cursor)


def test_decode_cursor_rejects_non_int_last_id() -> None:
    cursor = _encode_payload(
        {
            "last_id": "123",
            "last_created_at": datetime.now(UTC).isoformat(),
        }
    )

    with pytest.raises(ValueError, match="last_id"):
        PaginationCursor.decode_cursor(cursor)


def test_decode_cursor_round_trips_with_filters_hash() -> None:
    last_created_at = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    cursor = PaginationCursor.encode_cursor(
        last_id=42,
        last_created_at=last_created_at,
        filters={"content_type": ["podcast", "article"], "date": None},
    )

    cursor_data = PaginationCursor.decode_cursor(cursor)

    assert cursor_data.last_id == 42
    assert cursor_data.last_created_at == last_created_at
    assert cursor_data.filters_hash
    assert PaginationCursor.validate_cursor(
        cursor_data,
        {"content_type": ["article", "podcast"], "date": None},
    )
