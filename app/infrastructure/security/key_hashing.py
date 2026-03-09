"""Helpers for hashing and verifying API keys."""

from __future__ import annotations

import hashlib
import hmac


def hash_api_key(raw_key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def verify_api_key_hash(raw_key: str, key_hash: str) -> bool:
    """Verify an API key against a stored hash."""
    calculated = hash_api_key(raw_key)
    return hmac.compare_digest(calculated, key_hash)
