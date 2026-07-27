"""Read-only SQL validation for admin DB queries."""

from __future__ import annotations

import re

_ALLOWED_PREFIXES = ("select", "with", "pragma", "explain")
_READONLY_EXPLAIN = re.compile(
    r"^explain(?:\s+query\s+plan|\s+analyze|\s*\([^)]*\))?\s+(?:select|with)\b",
    re.IGNORECASE,
)
_FORBIDDEN_KEYWORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "replace",
    "truncate",
    "attach",
    "detach",
    "vacuum",
    "reindex",
    "begin",
    "commit",
    "rollback",
    "grant",
    "revoke",
}


def normalize_sql(sql: str) -> str:
    """Normalize a SQL string for validation/execution."""
    normalized = sql.strip()
    if normalized.endswith(";"):
        normalized = normalized[:-1].rstrip()
    return normalized


def validate_readonly_sql(sql: str) -> str:
    """Return normalized SQL if it is allowed, otherwise raise ValueError."""
    normalized = normalize_sql(sql)
    if not normalized:
        raise ValueError("SQL must not be empty")
    if ";" in normalized:
        raise ValueError("Only single-statement SQL is allowed")

    lowered = normalized.lower()
    if not lowered.startswith(_ALLOWED_PREFIXES):
        raise ValueError("Only read-only SELECT/WITH/PRAGMA/EXPLAIN statements are allowed")

    if lowered.startswith("explain") and _READONLY_EXPLAIN.match(normalized) is None:
        raise ValueError("EXPLAIN must target one read-only SELECT/WITH statement")

    keyword_hits = {
        keyword
        for keyword in _FORBIDDEN_KEYWORDS
        if re.search(rf"\b{re.escape(keyword)}\b", lowered) is not None
    }
    if keyword_hits:
        forbidden = ", ".join(sorted(keyword_hits))
        raise ValueError(f"Forbidden SQL keyword(s): {forbidden}")

    return normalized
