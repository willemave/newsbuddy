"""Local source-hint routing for long-form artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.models.metadata.longform_artifacts import ArtifactType


@dataclass(frozen=True)
class ArtifactSourceHint:
    """Deterministic candidate set for one artifact generation request."""

    source_hint: str
    candidates: list[ArtifactType]


RESEARCH_HOSTS = {
    "arxiv.org",
    "huggingface.co",
    "openreview.net",
    "paperswithcode.com",
    "pmc.ncbi.nlm.nih.gov",
    "nature.com",
}

GITHUB_HOSTS = {
    "github.com",
    "www.github.com",
    "gist.github.com",
    "raw.githubusercontent.com",
}

NEWS_HOSTS = {
    "news.ycombinator.com",
    "techmeme.com",
    "www.techmeme.com",
}

TWITTER_HOSTS = {
    "twitter.com",
    "www.twitter.com",
    "x.com",
    "www.x.com",
}


def _normalize_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _host(url: str | None) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    parsed = urlparse(url.strip())
    return parsed.netloc.strip().lower() or None


def resolve_artifact_source_hint(
    content_type: str,
    *,
    url: str | None,
    platform: str | None,
    metadata: Mapping[str, Any] | None = None,
) -> ArtifactSourceHint:
    """Return local artifact candidates from source metadata without an LLM call."""
    metadata_map = metadata or {}
    normalized_content_type = _normalize_string(content_type) or ""
    normalized_platform = _normalize_string(platform) or _normalize_string(
        metadata_map.get("platform")
    )
    normalized_host = _host(url)
    normalized_url = (url or "").strip().lower()
    metadata_content_type = _normalize_string(metadata_map.get("content_type"))

    if normalized_content_type == "news":
        return ArtifactSourceHint("news:briefing", ["briefing"])

    if (
        metadata_content_type == "pdf"
        or normalized_url.endswith(".pdf")
        or normalized_host in RESEARCH_HOSTS
        or (normalized_host == "huggingface.co" and "/papers/" in normalized_url)
    ):
        return ArtifactSourceHint("research:paper", ["findings", "mental_model", "briefing"])

    if normalized_platform == "github" or normalized_host in GITHUB_HOSTS:
        return ArtifactSourceHint("github:repo", ["walkthrough", "playbook", "mental_model"])

    if normalized_host in NEWS_HOSTS or normalized_platform in {"hackernews", "techmeme"}:
        return ArtifactSourceHint("news:event", ["briefing"])

    if normalized_platform == "twitter" or normalized_host in TWITTER_HOSTS:
        return ArtifactSourceHint("social:announcement", ["briefing", "argument"])

    if normalized_content_type == "podcast":
        return ArtifactSourceHint("podcast:conversation", ["playbook", "portrait", "mental_model"])

    if normalized_platform == "substack" or (
        normalized_host is not None and normalized_host.endswith(".substack.com")
    ):
        return ArtifactSourceHint("substack:analysis", ["argument", "mental_model", "findings"])

    return ArtifactSourceHint(
        "article:general",
        ["argument", "mental_model", "briefing", "playbook"],
    )
