"""Canonical Learning Deck canvas and layout-version contract."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class LearningDeckCanvas:
    """One Reveal canvas used by the hosted viewer."""

    width: int
    height: int
    margin: float

    @property
    def label(self) -> str:
        return f"{self.width} × {self.height}"

    def as_viewer_config(self) -> dict[str, int | float]:
        return {
            "width": self.width,
            "height": self.height,
            "margin": self.margin,
        }


@dataclass(frozen=True)
class LearningDeckLayoutProfile:
    """Versioned responsive layout selected by generated deck metadata."""

    meta_name: str
    version: str
    portrait: LearningDeckCanvas
    landscape: LearningDeckCanvas

    @property
    def meta_tag(self) -> str:
        return f'<meta name="{self.meta_name}" content="{self.version}">'


LEARNING_DECK_PHONE_BREAKPOINT = 700
LEARNING_DECK_REVEAL_VERSION = "6.0.1"
LEARNING_DECK_DESKTOP_CANVAS = LearningDeckCanvas(width=1280, height=720, margin=0.025)
RESPONSIVE_LEARNING_DECK_LAYOUT = LearningDeckLayoutProfile(
    meta_name="newsly-deck-layout",
    version="responsive-v2",
    portrait=LearningDeckCanvas(width=720, height=1280, margin=0.005),
    landscape=LearningDeckCanvas(width=1280, height=720, margin=0.012),
)
LEGACY_LEARNING_DECK_LAYOUT = LearningDeckLayoutProfile(
    meta_name=RESPONSIVE_LEARNING_DECK_LAYOUT.meta_name,
    version="legacy",
    portrait=LearningDeckCanvas(width=1280, height=960, margin=0.005),
    landscape=LearningDeckCanvas(width=1280, height=860, margin=0.012),
)


def learning_deck_prompt_values() -> dict[str, str]:
    """Return layout values substituted into generation prompts and guides."""
    return {
        "responsive_layout_meta_tag": RESPONSIVE_LEARNING_DECK_LAYOUT.meta_tag,
        "responsive_layout_version": RESPONSIVE_LEARNING_DECK_LAYOUT.version,
        "reveal_cdn_base_url": (
            f"https://cdn.jsdelivr.net/npm/reveal.js@{LEARNING_DECK_REVEAL_VERSION}"
        ),
        "portrait_canvas": RESPONSIVE_LEARNING_DECK_LAYOUT.portrait.label,
        "landscape_canvas": RESPONSIVE_LEARNING_DECK_LAYOUT.landscape.label,
    }


def learning_deck_viewer_profiles_json() -> str:
    """Serialize the complete viewer layout policy for the injected JavaScript."""
    profiles = {
        "phoneBreakpoint": LEARNING_DECK_PHONE_BREAKPOINT,
        "desktop": LEARNING_DECK_DESKTOP_CANVAS.as_viewer_config(),
        "responsive": {
            "portrait": RESPONSIVE_LEARNING_DECK_LAYOUT.portrait.as_viewer_config(),
            "landscape": RESPONSIVE_LEARNING_DECK_LAYOUT.landscape.as_viewer_config(),
        },
        "legacy": {
            "portrait": LEGACY_LEARNING_DECK_LAYOUT.portrait.as_viewer_config(),
            "landscape": LEGACY_LEARNING_DECK_LAYOUT.landscape.as_viewer_config(),
        },
    }
    return json.dumps(profiles, separators=(",", ":"))
