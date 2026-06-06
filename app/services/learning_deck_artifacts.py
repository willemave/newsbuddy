"""Artifact validation, rendering, and storage helpers for Learning Decks."""

from __future__ import annotations

import json
import mimetypes
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from html import escape
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

import markdown

from app.core.settings import get_settings
from app.services.gateways.object_storage_gateway import (
    ObjectStorageGateway,
    get_object_storage_gateway,
)

ALLOWED_EXTERNAL_SCRIPT_PACKAGES = frozenset(
    {
        "reveal.js",
        "react",
        "react-dom",
        "d3",
        "mermaid",
    }
)
ALLOWED_SCRIPT_CDN_HOSTS = frozenset(
    {
        "cdn.jsdelivr.net",
        "unpkg.com",
    }
)
HTML_CONTENT_TYPE = "text/html; charset=utf-8"
MARKDOWN_CONTENT_TYPE = "text/markdown; charset=utf-8"
JSONL_CONTENT_TYPE = "application/x-ndjson; charset=utf-8"


class LearningDeckArtifactError(ValueError):
    """Raised when a generated artifact cannot be accepted for hosting."""


@dataclass(frozen=True)
class StoredLearningDeckArtifact:
    """Object keys for one stored Learning Deck artifact bundle."""

    storage_prefix: str
    deck_object_key: str
    source_notes_object_key: str
    source_notes_html_object_key: str
    artifact_object_keys: list[str]


def validate_learning_deck_artifact(
    *,
    index_html: str,
    source_notes_md: str,
) -> None:
    """Validate generated deck and source notes before raw hosting."""
    settings = get_settings()
    errors: list[str] = []

    if not index_html.strip():
        errors.append("index.html is empty")
    if not source_notes_md.strip():
        errors.append("source-notes.md is empty")
    if len(index_html.encode("utf-8")) > settings.learning_deck_max_index_html_bytes:
        errors.append("index.html exceeds configured size limit")
    if len(source_notes_md.encode("utf-8")) > settings.learning_deck_max_source_notes_bytes:
        errors.append("source-notes.md exceeds configured size limit")
    if "<section" not in index_html.lower() or "reveal" not in index_html.lower():
        errors.append("index.html does not look like a Reveal.js deck")
    if re.search(r"\son[a-z]+\s*=", index_html, flags=re.IGNORECASE):
        errors.append("index.html contains inline event-handler attributes")
    if not _has_custom_visual_style(index_html):
        errors.append("index.html must include custom deck styling beyond stock Reveal.js")

    for script_src in _script_sources(index_html):
        if _is_allowed_script_src(script_src):
            continue
        errors.append(f"index.html contains disallowed script source: {script_src}")

    if not re.search(r"(?im)^#{1,3}\s+source", source_notes_md):
        errors.append("source-notes.md must include a source section")
    _append_secret_and_host_path_errors(
        errors,
        index_html=index_html,
        source_notes_md=source_notes_md,
    )

    if errors:
        raise LearningDeckArtifactError("; ".join(errors))


def render_source_notes_html(source_notes_md: str, *, title: str) -> str:
    """Render source notes Markdown to sanitized standalone HTML."""
    body = markdown.markdown(
        source_notes_md,
        extensions=["extra", "sane_lists", "toc"],
        output_format="html5",
    )
    body = _sanitize_source_notes_html(body)
    escaped_title = escape(title or "Source Notes")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.55;
      margin: 0;
      padding: 32px;
      color: #171717;
      background: #fff;
    }}
    main {{ max-width: 880px; margin: 0 auto; }}
    a {{ color: #0f5fff; }}
    pre, code {{ background: #f4f4f5; border-radius: 5px; }}
    pre {{ padding: 12px; overflow-x: auto; }}
    code {{ padding: 1px 4px; }}
    blockquote {{
      border-left: 3px solid #d4d4d8;
      color: #52525b;
      margin-left: 0;
      padding-left: 16px;
    }}
  </style>
</head>
<body>
  <main>
{body}
  </main>
</body>
</html>"""


def store_learning_deck_artifact(
    *,
    user_id: int,
    deck_id: int,
    run_id: int,
    index_html: str,
    source_notes_md: str,
    source_notes_html: str,
    extra_text_assets: dict[str, tuple[str, str]] | None = None,
    extra_assets: dict[str, tuple[bytes, str]] | None = None,
    gateway: ObjectStorageGateway | None = None,
) -> StoredLearningDeckArtifact:
    """Store one validated artifact bundle in object storage."""
    validate_learning_deck_artifact(index_html=index_html, source_notes_md=source_notes_md)
    settings = get_settings()
    storage_gateway = gateway or get_object_storage_gateway()
    prefix = build_learning_deck_storage_prefix(user_id=user_id, deck_id=deck_id, run_id=run_id)
    deck_key = f"{prefix}/index.html"
    notes_key = f"{prefix}/source-notes.md"
    notes_html_key = f"{prefix}/source-notes.html"
    object_keys = [deck_key, notes_key, notes_html_key]
    asset_count = len(extra_text_assets or {}) + len(extra_assets or {})
    if asset_count > settings.learning_deck_max_asset_count:
        raise LearningDeckArtifactError("artifact bundle has too many local assets")

    storage_gateway.put_text(key=deck_key, text=index_html, content_type=HTML_CONTENT_TYPE)
    storage_gateway.put_text(
        key=notes_key,
        text=source_notes_md,
        content_type=MARKDOWN_CONTENT_TYPE,
    )
    storage_gateway.put_text(
        key=notes_html_key,
        text=source_notes_html,
        content_type=HTML_CONTENT_TYPE,
    )

    for relative_path, (text, content_type) in (extra_text_assets or {}).items():
        normalized = normalize_artifact_relative_path(relative_path)
        key = f"{prefix}/{normalized}"
        storage_gateway.put_text(key=key, text=text, content_type=content_type)
        object_keys.append(key)

    for relative_path, (data, content_type) in (extra_assets or {}).items():
        if len(data) > settings.learning_deck_max_asset_bytes:
            raise LearningDeckArtifactError(f"artifact asset is too large: {relative_path}")
        normalized = normalize_artifact_relative_path(relative_path)
        key = f"{prefix}/{normalized}"
        storage_gateway.put_bytes(key=key, data=data, content_type=content_type)
        object_keys.append(key)

    for required_key in (deck_key, notes_key, notes_html_key):
        if not storage_gateway.exists(key=required_key):
            raise LearningDeckArtifactError(f"stored artifact is not available: {required_key}")

    return StoredLearningDeckArtifact(
        storage_prefix=prefix,
        deck_object_key=deck_key,
        source_notes_object_key=notes_key,
        source_notes_html_object_key=notes_html_key,
        artifact_object_keys=object_keys,
    )


def delete_learning_deck_objects(
    object_keys: Iterable[str],
    *,
    gateway: ObjectStorageGateway | None = None,
) -> None:
    """Delete all known object keys for a deck/run."""
    storage_gateway = gateway or get_object_storage_gateway()
    for key in sorted({key for key in object_keys if key}):
        storage_gateway.delete(key=key)


def read_learning_deck_object(
    key: str,
    *,
    gateway: ObjectStorageGateway | None = None,
) -> bytes:
    """Fetch one hosted Learning Deck object by internal storage key."""
    return (gateway or get_object_storage_gateway()).get_bytes(key=key)


def guess_learning_deck_content_type(relative_path: str) -> str:
    """Guess a content type for a hosted artifact path."""
    guessed, _encoding = mimetypes.guess_type(relative_path)
    return guessed or "application/octet-stream"


def store_learning_deck_agent_log(
    *,
    user_id: int,
    deck_id: int,
    run_id: int,
    events: list[dict[str, Any]],
    gateway: ObjectStorageGateway | None = None,
) -> str:
    """Store internal agent/tool logs outside the public artifact bundle."""
    key = build_learning_deck_agent_log_key(user_id=user_id, deck_id=deck_id, run_id=run_id)
    payload = "\n".join(json.dumps(event, sort_keys=True, default=str) for event in events)
    (gateway or get_object_storage_gateway()).put_text(
        key=key,
        text=f"{payload}\n" if payload else "",
        content_type=JSONL_CONTENT_TYPE,
    )
    return key


def build_learning_deck_storage_prefix(*, user_id: int, deck_id: int, run_id: int) -> str:
    """Return the object-storage prefix for one run."""
    settings = get_settings()
    root_prefix = settings.storage.content_body_storage_prefix.strip("/")
    parts = [
        part
        for part in (
            root_prefix,
            "learning_decks",
            str(user_id),
            str(deck_id),
            "runs",
            str(run_id),
        )
        if part
    ]
    return "/".join(parts)


def build_learning_deck_agent_log_key(*, user_id: int, deck_id: int, run_id: int) -> str:
    """Return the internal, non-user-facing agent log key for one run."""
    settings = get_settings()
    root_prefix = settings.storage.content_body_storage_prefix.strip("/")
    parts = [
        part
        for part in (
            root_prefix,
            "learning_deck_internal_logs",
            str(user_id),
            str(deck_id),
            "runs",
            str(run_id),
            "agent-log.jsonl",
        )
        if part
    ]
    return "/".join(parts)


def normalize_artifact_relative_path(relative_path: str) -> str:
    """Normalize an artifact path and ensure it stays relative."""
    cleaned = relative_path.strip().lstrip("/")
    candidate = PurePosixPath(cleaned)
    if not cleaned or candidate.is_absolute() or ".." in candidate.parts:
        raise LearningDeckArtifactError("Artifact path must stay within the bundle")
    return candidate.as_posix()


def _script_sources(index_html: str) -> list[str]:
    return re.findall(
        r"<script\b[^>]*\bsrc\s*=\s*['\"]([^'\"]+)['\"][^>]*>",
        index_html,
        flags=re.IGNORECASE,
    )


def _is_allowed_script_src(src: str) -> bool:
    normalized = src.strip().lower()
    if not normalized:
        return False
    if _is_local_artifact_path(normalized):
        return True
    return _is_allowed_external_script_url(normalized)


def _is_local_artifact_path(src: str) -> bool:
    if src.startswith("//"):
        return False
    parsed = urlparse(src)
    if parsed.scheme or parsed.netloc:
        return False
    try:
        normalize_artifact_relative_path(parsed.path)
    except LearningDeckArtifactError:
        return False
    return True


def _is_allowed_external_script_url(src: str) -> bool:
    parsed = urlparse(src)
    if parsed.scheme != "https":
        return False
    host = parsed.netloc.lower()
    if host not in ALLOWED_SCRIPT_CDN_HOSTS:
        return False
    package = _cdn_package_name(host=host, path=parsed.path)
    return package in ALLOWED_EXTERNAL_SCRIPT_PACKAGES


def _cdn_package_name(*, host: str, path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if host == "cdn.jsdelivr.net":
        if len(parts) < 2 or parts[0] != "npm":
            return None
        return _package_name_from_cdn_segment(parts[1])
    if host == "unpkg.com":
        if not parts:
            return None
        return _package_name_from_cdn_segment(parts[0])
    return None


def _package_name_from_cdn_segment(segment: str) -> str:
    if segment.startswith("@"):
        scoped_parts = segment.split("@", 2)
        return "@".join(scoped_parts[:2]) if len(scoped_parts) >= 2 else segment
    return segment.split("@", 1)[0]


def _stylesheet_hrefs(index_html: str) -> list[str]:
    hrefs: list[str] = []
    for tag in re.findall(r"<link\b[^>]*>", index_html, flags=re.IGNORECASE):
        rel = re.search(r"\brel\s*=\s*['\"]([^'\"]+)['\"]", tag, flags=re.IGNORECASE)
        if rel is None or "stylesheet" not in rel.group(1).lower().split():
            continue
        href = re.search(r"\bhref\s*=\s*['\"]([^'\"]+)['\"]", tag, flags=re.IGNORECASE)
        if href is not None:
            hrefs.append(href.group(1))
    return hrefs


def _has_custom_visual_style(index_html: str) -> bool:
    if re.search(r"<style\b[^>]*>.*?</style>", index_html, flags=re.IGNORECASE | re.DOTALL):
        return True

    for href in _stylesheet_hrefs(index_html):
        normalized = href.strip().lower()
        if not normalized:
            continue
        if _is_reveal_stylesheet_url(normalized):
            continue
        return True
    return False


def _is_reveal_stylesheet_url(href: str) -> bool:
    if _is_local_artifact_path(href):
        return False
    parsed = urlparse(href)
    if parsed.scheme != "https":
        return False
    host = parsed.netloc.lower()
    if host not in ALLOWED_SCRIPT_CDN_HOSTS:
        return False
    return _cdn_package_name(host=host, path=parsed.path) == "reveal.js"


def _sanitize_source_notes_html(html: str) -> str:
    try:
        from lxml_html_clean import Cleaner
    except ImportError:
        return re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.IGNORECASE | re.DOTALL)

    cleaner = Cleaner(
        scripts=True,
        javascript=True,
        comments=True,
        style=False,
        links=False,
        meta=False,
        page_structure=False,
        safe_attrs_only=True,
    )
    return cleaner.clean_html(html)


def _append_secret_and_host_path_errors(
    errors: list[str],
    *,
    index_html: str,
    source_notes_md: str,
) -> None:
    settings = get_settings()
    combined = f"{index_html}\n{source_notes_md}"
    secret_values = [
        settings.JWT_SECRET_KEY,
        settings.ADMIN_PASSWORD,
        settings.openai_api_key,
        settings.anthropic_api_key,
        settings.google_api_key,
        settings.openrouter_api_key,
        settings.cerebras_api_key,
        settings.exa_api_key,
        settings.learning_sandbox_e2b_api_key,
    ]
    for value in secret_values:
        if isinstance(value, str) and len(value) >= 12 and value in combined:
            errors.append("artifact appears to contain a configured secret value")
            break

    cwd = os.getcwd()
    suspicious_paths = {
        cwd,
        str(settings.logs_dir),
        str(settings.content_body_root_dir),
        "/Users/",
    }
    if any(path and path in combined for path in suspicious_paths):
        errors.append("artifact appears to expose backend host filesystem paths")
