"""Canonical Briefing figure-placement policy."""

from __future__ import annotations

from typing import Any

from app.models.contracts import BriefingFigureAlignment, BriefingFigurePlacement


def canonical_figure_placement(value: Any) -> BriefingFigurePlacement:
    """Return a supported placement, preferring inset for absent or invalid hints."""

    if isinstance(value, BriefingFigurePlacement):
        return value
    try:
        return BriefingFigurePlacement(str(value).strip().lower())
    except (TypeError, ValueError):
        return BriefingFigurePlacement.INSET


def canonical_figure_alignment(
    value: Any,
    *,
    fallback: BriefingFigureAlignment = BriefingFigureAlignment.RIGHT,
) -> BriefingFigureAlignment:
    """Return a supported horizontal alignment, using the supplied alternating fallback."""

    if isinstance(value, BriefingFigureAlignment):
        return value
    try:
        return BriefingFigureAlignment(str(value).strip().lower())
    except (TypeError, ValueError):
        return fallback


def alternating_figure_alignment(index: int) -> BriefingFigureAlignment:
    """Start on the right, then alternate inline figures across the page."""

    return BriefingFigureAlignment.RIGHT if index % 2 == 0 else BriefingFigureAlignment.LEFT
