"""Pagination utilities for cursor-based pagination with opaque tokens."""

import base64
import binascii
import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictInt, ValidationError


class CursorData(BaseModel):
    """Validated payload stored inside an opaque pagination cursor."""

    model_config = ConfigDict(extra="forbid")

    last_id: StrictInt
    last_created_at: datetime
    filters_hash: str | None = None
    last_rank: float | None = None


class PaginationCursor:
    """Utility class for encoding/decoding pagination cursors."""

    @staticmethod
    def encode_cursor(
        last_id: int,
        last_created_at: datetime,
        filters: dict[str, Any] | None = None,
        last_rank: float | None = None,
    ) -> str:
        """Encode pagination state into an opaque cursor token.

        Args:
            last_id: ID of the last item in the current page
            last_created_at: Created timestamp of the last item
            filters: Optional dict of filter parameters for validation

        Returns:
            Base64-encoded cursor string
        """
        cursor_data = {
            "last_id": last_id,
            "last_created_at": last_created_at.isoformat(),
        }
        if last_rank is not None:
            cursor_data["last_rank"] = last_rank

        # Add filters hash for validation
        if filters:
            cursor_data["filters_hash"] = PaginationCursor._hash_filters(filters)

        # Encode as JSON then base64
        json_str = json.dumps(cursor_data, sort_keys=True)
        return base64.urlsafe_b64encode(json_str.encode()).decode()

    @staticmethod
    def decode_cursor(cursor: str) -> CursorData:
        """Decode an opaque cursor token into pagination state.

        Args:
            cursor: Base64-encoded cursor string

        Returns:
            Validated cursor data with last_id, last_created_at, and optional filters_hash

        Raises:
            ValueError: If cursor is invalid or malformed
        """
        try:
            # Decode base64 then JSON
            json_str = base64.urlsafe_b64decode(cursor.encode()).decode()
            cursor_data = json.loads(json_str)
            if not isinstance(cursor_data, dict):
                raise ValueError("Cursor payload must be a JSON object")

            return CursorData.model_validate(cursor_data)
        except (
            UnicodeDecodeError,
            binascii.Error,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ) as e:
            raise ValueError(f"Invalid pagination cursor: {str(e)}") from e

    @staticmethod
    def validate_cursor(cursor_data: CursorData, current_filters: dict[str, Any]) -> bool:
        """Validate that cursor filters match current request filters.

        Args:
            cursor_data: Decoded cursor data
            current_filters: Current request filter parameters

        Returns:
            True if filters match or no filter hash in cursor, False otherwise
        """
        cursor_hash = cursor_data.filters_hash
        if not isinstance(cursor_hash, str) or not cursor_hash:
            # No filter hash in cursor, assume valid for backwards compatibility
            return True

        current_hash = PaginationCursor._hash_filters(current_filters)
        return cursor_hash == current_hash

    @staticmethod
    def _hash_filters(filters: dict[str, Any]) -> str:
        """Create a hash of filter parameters for validation.

        Args:
            filters: Dict of filter parameters

        Returns:
            SHA256 hash of normalized filter params
        """
        # Normalize filters (remove None values, sort keys)
        normalized: dict[str, Any] = {}
        for k, v in sorted(filters.items()):
            if v is not None:
                # Sort lists to ensure consistent hashing
                if isinstance(v, list):
                    normalized[k] = sorted(v)
                else:
                    normalized[k] = v
        json_str = json.dumps(normalized, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()
