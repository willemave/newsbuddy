"""Canonical Briefing figure-placement policy."""

from __future__ import annotations

from typing import Any

from app.models.contracts import BriefingFigurePlacement


def canonical_figure_placement(value: Any) -> BriefingFigurePlacement:
    """Return a supported placement, preferring inset for absent or invalid hints."""

    if isinstance(value, BriefingFigurePlacement):
        return value
    try:
        return BriefingFigurePlacement(str(value).strip().lower())
    except (TypeError, ValueError):
        return BriefingFigurePlacement.INSET
