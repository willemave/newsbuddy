"""View-time wrapper for hosted Learning Deck HTML."""

from __future__ import annotations

import re
from functools import cache

from app.services.learning_deck_artifacts import has_responsive_learning_deck_layout
from app.services.learning_deck_layout import learning_deck_viewer_profiles_json

NAVIGATION_CONTROLS_MARKER = "data-newsly-learning-deck-controls"


def with_learning_deck_navigation_controls(data: bytes) -> bytes:
    """Add the themed mobile viewer shell to generated Reveal.js decks."""
    try:
        html = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if NAVIGATION_CONTROLS_MARKER in html:
        return data

    injected_html = learning_deck_navigation_controls_html(
        responsive_layout=has_responsive_learning_deck_layout(html)
    )
    match = re.search(r"</body\s*>", html, flags=re.IGNORECASE)
    if match is None:
        return f"{html}\n{injected_html}".encode()
    return f"{html[: match.start()]}\n{injected_html}\n{html[match.start() :]}".encode()


@cache
def learning_deck_navigation_controls_html(*, responsive_layout: bool = False) -> str:
    """Return the hosted viewer shell CSS and JS."""
    responsive_layout_js = str(responsive_layout).lower()
    layout_profiles_js = learning_deck_viewer_profiles_json()
    return (
        _house_deck_theme_html()
        + f"""<style {NAVIGATION_CONTROLS_MARKER}="style">
  html,
  body {{
    width: 100%;
    height: 100%;
    margin: 0;
    overflow: hidden;
    overscroll-behavior: none;
  }}
  .reveal {{
    width: 100vw !important;
    height: 100vh !important;
    height: 100dvh !important;
    min-height: 100vh !important;
    overflow: hidden !important;
  }}
  .reveal .slides section {{
    box-sizing: border-box;
    max-width: 100%;
  }}
  .reveal .slides section > * {{
    max-width: 100%;
  }}
  .reveal img,
  .reveal svg,
  .reveal video,
  .reveal canvas {{
    max-width: 100%;
    height: auto;
  }}
  html.newsly-learning-deck-responsive.newsly-learning-deck-portrait .reveal .controls {{
    bottom: 92px !important;
  }}
  html.newsly-learning-deck-landscape .reveal .slides section {{
    padding: 30px 48px !important;
  }}
  html.newsly-learning-deck-landscape .reveal h1 {{
    font-size: 2.55em;
  }}
  html.newsly-learning-deck-landscape .reveal h2 {{
    font-size: 1.55em;
  }}
  html.newsly-learning-deck-landscape .reveal .slide-title {{
    margin-bottom: 14px !important;
    padding-bottom: 10px !important;
  }}
</style>
<script {NAVIGATION_CONTROLS_MARKER}="script">
(function () {{
  var isResponsiveDeck = {responsive_layout_js};
  var layoutProfiles = {layout_profiles_js};

  function withReveal(callback) {{
    if (window.Reveal && typeof window.Reveal.configure === "function") {{
      callback(window.Reveal);
      return;
    }}
    window.setTimeout(function () {{ withReveal(callback); }}, 150);
  }}

  withReveal(function (reveal) {{
    function isPortraitOrientation() {{
      if (typeof window.matchMedia === "function") {{
        return window.matchMedia("(orientation: portrait)").matches;
      }}
      return window.innerHeight >= window.innerWidth;
    }}

    function fitConfig() {{
      var screenWidth = window.screen && window.screen.width
        ? window.screen.width
        : window.innerWidth;
      var screenHeight = window.screen && window.screen.height
        ? window.screen.height
        : window.innerHeight;
      var smallestScreenSide = Math.min(screenWidth, screenHeight);
      var isPortrait = isPortraitOrientation();
      var isPhoneSized = smallestScreenSide < layoutProfiles.phoneBreakpoint;
      var phoneProfile = isResponsiveDeck
        ? layoutProfiles.responsive
        : layoutProfiles.legacy;
      var canvas = isPhoneSized
        ? phoneProfile[isPortrait ? "portrait" : "landscape"]
        : layoutProfiles.desktop;
      document.documentElement.classList.toggle(
        "newsly-learning-deck-portrait",
        isPhoneSized && isPortrait
      );
      document.documentElement.classList.toggle(
        "newsly-learning-deck-landscape",
        isPhoneSized && !isPortrait
      );
      document.documentElement.classList.toggle(
        "newsly-learning-deck-responsive",
        isResponsiveDeck
      );
      return {{
        controls: true,
        progress: false,
        width: canvas.width,
        height: canvas.height,
        margin: canvas.margin,
        center: false,
        minScale: 0.05,
        maxScale: 3,
        view: "slide",
        scrollActivationWidth: null
      }};
    }}

    function applyFit() {{
      reveal.configure(fitConfig());
      if (typeof reveal.toggleScrollView === "function") {{
        reveal.toggleScrollView(false);
      }}
    }}
    var fitAnimationFrame = null;
    function scheduleFit() {{
      if (fitAnimationFrame !== null) return;
      fitAnimationFrame = window.requestAnimationFrame(function () {{
        fitAnimationFrame = null;
        applyFit();
      }});
    }}
    reveal.configure(fitConfig());
    if (typeof reveal.on === "function") {{
      reveal.on("ready", scheduleFit);
    }}
    scheduleFit();
    window.setTimeout(scheduleFit, 250);
    window.setTimeout(scheduleFit, 1000);
    window.addEventListener("resize", scheduleFit);
    if (window.visualViewport) {{
      window.visualViewport.addEventListener("resize", scheduleFit);
    }}
  }});
}}());
</script>"""
    )


def _house_deck_theme_html() -> str:
    """Return the house Daylight theme (fonts + CSS) for view-time injection."""
    from app.services.learning_deck_theme import (
        DECK_FONT_LINKS,
        DECK_THEME_CSS,
        DECK_THEME_STYLE_ID,
    )

    return (
        DECK_FONT_LINKS + '<style id="' + DECK_THEME_STYLE_ID + '">' + DECK_THEME_CSS + "</style>"
    )
