"""Helpers for normalizing and resolving display titles."""

from __future__ import annotations

import re
from html import unescape
from typing import Any

MAX_TITLE_CHARS = 500
MAX_SUMMARY_EXCERPT_CHARS = 120

_PLACEHOLDER_TITLE_PATTERNS = (re.compile(r"skill\d+", re.IGNORECASE),)
_PLACEHOLDER_TITLE_VALUES = {
    "na",
    "n/a",
    "none",
    "unknown",
    "untitled",
    "void",
}
_BLOCKED_TITLE_VALUES = {
    "access denied",
    "attention required!",
    "enable javascript and cookies to continue",
    "forbes.com",
    "fastcompany.com",
    "please verify you are a human",
    "subscribe to read",
    "wsj.com",
}
_BLOCKED_TITLE_PREFIXES = (
    "just a moment",
    "verification required",
)
_BARE_DOMAIN_TITLE_PATTERN = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,63}/?", re.IGNORECASE)


def _is_blocked_page_title(title: str) -> bool:
    normalized = title.casefold().strip(" .!?:;-")
    if normalized in _BLOCKED_TITLE_VALUES:
        return True
    if any(normalized.startswith(prefix) for prefix in _BLOCKED_TITLE_PREFIXES):
        return True
    return " " not in normalized and bool(_BARE_DOMAIN_TITLE_PATTERN.fullmatch(normalized))


def clean_title(value: Any) -> str | None:
    """Normalize a title and drop obvious placeholder values."""
    if not isinstance(value, str):
        return None

    title = unescape(value)
    title = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", title)
    title = re.sub(r"(?is)<[^>]+>", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    if not title:
        return None

    normalized = title.casefold()
    if normalized in _PLACEHOLDER_TITLE_VALUES:
        return None
    if any(pattern.fullmatch(title) for pattern in _PLACEHOLDER_TITLE_PATTERNS):
        return None
    if _is_blocked_page_title(title):
        return None

    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS].rstrip()
    return title


def summarize_text_as_title(value: Any) -> str | None:
    """Turn summary text into a short title-like fallback."""
    if not isinstance(value, str):
        return None

    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return None

    if len(text) <= MAX_SUMMARY_EXCERPT_CHARS:
        return text

    sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    if 20 <= len(sentence) <= MAX_SUMMARY_EXCERPT_CHARS:
        return sentence

    excerpt = text[:MAX_SUMMARY_EXCERPT_CHARS].rstrip()
    if len(text) > MAX_SUMMARY_EXCERPT_CHARS:
        return f"{excerpt}…"
    return excerpt


def resolve_title_candidate(
    *candidates: Any,
    summary_text: str | None = None,
) -> str | None:
    """Resolve the strongest non-placeholder title candidate, if one exists."""
    for candidate in candidates:
        cleaned = clean_title(candidate)
        if cleaned:
            return cleaned
    return summarize_text_as_title(summary_text)


def resolve_display_title(
    *candidates: Any,
    summary_text: str | None = None,
    fallback: str = "Untitled",
) -> str:
    """Resolve the most useful display title from several candidates."""
    return resolve_title_candidate(*candidates, summary_text=summary_text) or fallback
