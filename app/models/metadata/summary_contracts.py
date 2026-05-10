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
    if "editorial_narrative" in summary:
        return SummaryKind.LONG_EDITORIAL_NARRATIVE
    if "insights" in summary:
        return SummaryKind.LONG_INTERLEAVED
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


def is_structured_summary_payload(
    summary: Mapping[str, Any] | None,
    raw_kind: Any,
) -> bool:
    """Return True when metadata contains a structured summary payload."""
    kind = resolve_summary_kind(summary, raw_kind)
    return kind == SummaryKind.LONG_STRUCTURED
