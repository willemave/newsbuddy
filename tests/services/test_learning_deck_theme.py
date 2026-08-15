from __future__ import annotations

import re

from app.services.learning_deck_agent import LEARNING_DECK_DESIGN_BRIEF, LEARNING_DECK_SYSTEM_PROMPT
from app.services.learning_deck_theme import (
    DECK_DESIGN_GUIDE,
    DECK_THEME_CSS,
    DECK_THEME_STYLE_ID,
)
from app.services.learning_deck_viewer import (
    learning_deck_navigation_controls_html,
    with_learning_deck_navigation_controls,
)

_SAMPLE_DECK = (
    b"<!doctype html><html><head>"
    b'<meta name="newsly-deck-layout" content="responsive-v2">'
    b'<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js/dist/reveal.css">'
    b"<style>.reveal { color: red; }</style></head><body>"
    b'<div class="reveal"><div class="slides">'
    b"<section><h2>Hello</h2><ul><li>One</li></ul></section>"
    b"</div></div>"
    b'<script src="https://cdn.jsdelivr.net/npm/reveal.js/dist/reveal.js"></script>'
    b"<script>Reveal.initialize();</script></body></html>"
)


def test_theme_uses_daylight_palette_and_type() -> None:
    assert "#1f6f5c" in DECK_THEME_CSS  # single emerald accent
    assert "#fbfaf8" in DECK_THEME_CSS  # warm paper base
    assert "Space Grotesk" in DECK_THEME_CSS
    assert "Spline Sans" in DECK_THEME_CSS
    assert "#000000" not in DECK_THEME_CSS  # never pure black
    assert "linear-gradient" not in DECK_THEME_CSS
    assert ".reveal .slides section:not(.stack)" in DECK_THEME_CSS
    assert ".reveal .slide-frame" in DECK_THEME_CSS
    assert "background: var(--selection-bg)" in DECK_THEME_CSS
    assert "color: var(--ink)" in DECK_THEME_CSS


def test_design_guide_documents_house_classes() -> None:
    for token in ("slide--cover", "slide--split", "slide--statement", "eyebrow", "bullets--cols"):
        assert token in DECK_DESIGN_GUIDE
    # The agent brief embeds the house guide so slides are authored with the classes.
    assert DECK_DESIGN_GUIDE.splitlines()[0] in LEARNING_DECK_DESIGN_BRIEF


def test_generation_prompt_documents_rich_react_diagram_authoring() -> None:
    assert "React/JSX" in LEARNING_DECK_SYSTEM_PROMPT
    assert "ReactDOM" in LEARNING_DECK_SYSTEM_PROMPT
    assert "D3" in LEARNING_DECK_SYSTEM_PROMPT
    assert "Mermaid" in LEARNING_DECK_SYSTEM_PROMPT
    assert "local scripts under `output/assets/`" in LEARNING_DECK_SYSTEM_PROMPT
    assert "Do not rely on browser Babel" in LEARNING_DECK_SYSTEM_PROMPT
    assert "Diagram-First Patterns" in LEARNING_DECK_DESIGN_BRIEF
    assert "Rich JavaScript and React Authoring" in LEARNING_DECK_DESIGN_BRIEF
    assert "Runtime React is allowed" in LEARNING_DECK_DESIGN_BRIEF
    assert "output/assets/deck.js" in LEARNING_DECK_DESIGN_BRIEF
    assert "roughly every two or three slides" in LEARNING_DECK_DESIGN_BRIEF
    assert "r-fit-text" in LEARNING_DECK_DESIGN_BRIEF
    assert "r-stretch" in LEARNING_DECK_DESIGN_BRIEF
    assert "stable, human-readable `id` attributes" in LEARNING_DECK_DESIGN_BRIEF
    assert "720 × 1280 portrait canvas" in LEARNING_DECK_DESIGN_BRIEF
    assert 'name="newsly-deck-layout" content="responsive-v2"' in LEARNING_DECK_SYSTEM_PROMPT


def test_viewer_markup_carries_theme_and_uses_reveal_navigation() -> None:
    markup = learning_deck_navigation_controls_html(responsive_layout=True)
    assert f'id="{DECK_THEME_STYLE_ID}"' in markup
    assert "Space+Grotesk" in markup  # Google Fonts link present
    assert "isResponsiveDeck = true" in markup
    assert '"portrait":{"width":720,"height":1280,"margin":0.005}' in markup
    assert '"landscape":{"width":1280,"height":720,"margin":0.012}' in markup
    assert 'window.matchMedia("(orientation: portrait)")' in markup
    assert "visualViewport.height" not in markup
    assert "newsly-learning-deck-responsive" in markup
    assert "data-newsly-learning-deck-fullscreen" not in markup
    assert "data-newsly-learning-deck-prev" not in markup
    assert "data-newsly-learning-deck-next" not in markup
    assert "controls: true" in markup
    assert "progress: false" in markup
    assert "scrollActivationWidth: null" in markup  # keep Reveal 5 in slide mode on phones
    assert 'view: "slide"' in markup
    assert "minScale: 0.05" in markup and "maxScale: 3" in markup
    assert 'window.addEventListener("resize", scheduleFit)' in markup
    assert 'reveal.on("slidechanged", scheduleFit)' in markup
    assert "backdrop-filter" not in markup  # glassmorphism removed


def test_augmented_deck_injects_theme_once_and_is_idempotent() -> None:
    augmented = with_learning_deck_navigation_controls(_SAMPLE_DECK).decode()
    assert f'id="{DECK_THEME_STYLE_ID}"' in augmented
    assert augmented.count(f'id="{DECK_THEME_STYLE_ID}"') == 1
    assert "__newslySlideModePatched" in augmented
    assert "isResponsiveDeck = true" in augmented
    assert augmented.index(DECK_THEME_STYLE_ID) < augmented.lower().index("</body>")
    again = with_learning_deck_navigation_controls(augmented.encode()).decode()
    assert again == augmented


def test_unmarked_stored_deck_keeps_legacy_viewer_fit() -> None:
    legacy_deck = _SAMPLE_DECK.replace(
        b'<meta name="newsly-deck-layout" content="responsive-v2">',
        b"",
    )

    augmented = with_learning_deck_navigation_controls(legacy_deck).decode()

    assert "isResponsiveDeck = false" in augmented


def test_augmented_deck_has_no_inline_event_handlers() -> None:
    augmented = with_learning_deck_navigation_controls(_SAMPLE_DECK).decode()
    assert re.search(r"\son[a-z]+\s*=", augmented, flags=re.IGNORECASE) is None
