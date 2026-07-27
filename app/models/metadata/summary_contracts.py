"""Canonical helpers for summary kind/version interpretation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.models.contracts import SummaryKind, SummaryVersion


def parse_summary_kind(raw_kind: Any) -> SummaryKind | None:
    """Parse a raw summary kind value into canonical enum form."""
    if isinstance(raw_kind, SummaryKind):
        return raw_kind
    if isinstance(raw_kind, str):
        try:
            return SummaryKind(raw_kind)
        except ValueError:
            return None
    return None


def parse_summary_version(raw_version: Any) -> SummaryVersion | None:
    """Parse a raw summary version value into canonical enum form."""
    if isinstance(raw_version, SummaryVersion):
        return raw_version
    if isinstance(raw_version, int):
        try:
            return SummaryVersion(raw_version)
        except ValueError:
            return None
    if isinstance(raw_version, str):
        try:
            return SummaryVersion(int(raw_version))
        except (TypeError, ValueError):
            return None
    return None


def infer_summary_kind(summary: Mapping[str, Any] | None) -> SummaryKind | None:
    """Infer summary kind from payload keys for legacy/partial metadata."""
    if not isinstance(summary, Mapping):
        return None
    if "artifact" in summary and "selection_trace" in summary:
        return SummaryKind.LONGFORM_ARTIFACT
    if summary.get("summary_type") == "interleaved":
        return SummaryKind.LONG_INTERLEAVED
    if "key_points" in summary and "topics" in summary:
        return SummaryKind.LONG_INTERLEAVED
    if "insights" in summary:
        return SummaryKind.LONG_INTERLEAVED
    if "points" in summary:
        return SummaryKind.LONG_BULLETS
    if "editorial_narrative" in summary:
        return SummaryKind.LONG_EDITORIAL_NARRATIVE
    if "summary" in summary and "key_points" in summary:
        return SummaryKind.SHORT_NEWS
    if "overview" in summary and "bullet_points" in summary:
        return SummaryKind.LONG_STRUCTURED
    if "bullet_points" in summary:
        return SummaryKind.LONG_BULLETS
    if "summary" in summary:
        return SummaryKind.SHORT_NEWS
    return None


def resolve_summary_kind(
    summary: Mapping[str, Any] | None,
    raw_kind: Any,
) -> SummaryKind | None:
    """Return canonical summary kind using explicit value with payload fallback."""
    parsed = parse_summary_kind(raw_kind)
    if parsed is not None:
        return parsed
    return infer_summary_kind(summary)


def resolve_summary_version(
    summary: Mapping[str, Any] | None,
    summary_kind: SummaryKind,
    raw_version: Any,
) -> SummaryVersion:
    """Return canonical summary version using explicit value with payload fallback."""
    parsed = parse_summary_version(raw_version)
    if parsed is not None:
        return parsed
    if summary_kind == SummaryKind.LONG_INTERLEAVED:
        if isinstance(summary, Mapping) and "key_points" in summary:
            return SummaryVersion.V2
        return SummaryVersion.V1
    if summary_kind == SummaryKind.LONG_EDITORIAL_NARRATIVE:
        if isinstance(summary, Mapping) and "source_details" in summary:
            return SummaryVersion.V2
        return SummaryVersion.V1
    return SummaryVersion.V1


def infer_summary_kind_version(
    content_type: str,
    summary: Mapping[str, Any] | None,
    raw_kind: Any,
    raw_version: Any,
) -> tuple[SummaryKind, SummaryVersion] | None:
    """Infer summary kind/version from explicit metadata and legacy payload shape."""
    summary_kind = resolve_summary_kind(summary, raw_kind)
    if summary_kind is None and content_type == "news":
        summary_kind = SummaryKind.SHORT_NEWS
    if summary_kind is None:
        return None
    return summary_kind, resolve_summary_version(summary, summary_kind, raw_version)
