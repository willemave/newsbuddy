"""View-time wrapper for hosted Learning Deck HTML."""

from __future__ import annotations

import re
from functools import cache

from app.services.learning_deck_artifacts import has_responsive_learning_deck_layout
from app.services.learning_deck_layout import learning_deck_viewer_profiles_json

NAVIGATION_CONTROLS_MARKER = "data-newsly-learning-deck-controls"
REVEAL_SLIDE_MODE_PATCH_MARKER = "__newslySlideModePatched"


def with_learning_deck_navigation_controls(data: bytes) -> bytes:
    """Add the themed mobile viewer shell to generated Reveal.js decks."""
    try:
        html = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if NAVIGATION_CONTROLS_MARKER in html:
        return data

    html = with_reveal_slide_mode_patch(html)
    injected_html = learning_deck_navigation_controls_html(
        responsive_layout=has_responsive_learning_deck_layout(html)
    )
    match = re.search(r"</body\s*>", html, flags=re.IGNORECASE)
    if match is None:
        return f"{html}\n{injected_html}".encode()
    return f"{html[: match.start()]}\n{injected_html}\n{html[match.start() :]}".encode()


def with_reveal_slide_mode_patch(html: str) -> str:
    """Patch generated Reveal initialization to keep phone viewers in slide mode."""
    if REVEAL_SLIDE_MODE_PATCH_MARKER in html:
        return html
    patch = f"""if (window.Reveal && !window.Reveal.{REVEAL_SLIDE_MODE_PATCH_MARKER}) {{
    window.Reveal.{REVEAL_SLIDE_MODE_PATCH_MARKER} = true;
    window.Reveal.__newslyOriginalInitialize = window.Reveal.initialize.bind(window.Reveal);
    window.Reveal.initialize = function (config) {{
      return window.Reveal.__newslyOriginalInitialize(Object.assign(
        {{}},
        config || {{}},
        {{ view: "slide", scrollActivationWidth: null }}
      ));
    }};
  }}
  """
    return re.sub(r"\bReveal\.initialize\s*\(", patch + "Reveal.initialize(", html, count=1)


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
  .newsly-learning-deck-controls {{
    position: fixed;
    right: 22px;
    bottom: calc(env(safe-area-inset-bottom, 0px) + 108px);
    z-index: 2147483647;
    display: flex;
    gap: 10px;
    align-items: center;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    pointer-events: auto;
  }}
  .newsly-learning-deck-controls button {{
    width: 46px;
    height: 46px;
    border: 1px solid var(--line-strong, rgba(27, 27, 26, 0.24));
    border-radius: 999px;
    background: var(--paper, #fbfaf8);
    color: var(--ink, #1b1b1a);
    box-shadow: 0 14px 34px -24px rgba(27, 27, 26, 0.65);
    font-family: "Spline Sans", system-ui, sans-serif;
    font-size: 24px;
    line-height: 1;
    font-weight: 500;
    display: grid;
    place-items: center;
    cursor: pointer;
    touch-action: manipulation;
    -webkit-user-select: none;
    user-select: none;
    transition: border-color 0.15s ease, color 0.15s ease, transform 0.15s ease;
  }}
  .newsly-learning-deck-controls button:hover {{
    border-color: var(--accent, #1f6f5c);
    color: var(--accent, #1f6f5c);
  }}
  .newsly-learning-deck-controls button:active {{
    transform: translateY(1px);
  }}
  .newsly-learning-deck-controls button.is-unavailable {{
    opacity: 0.35;
  }}
  html.newsly-learning-deck-portrait .newsly-learning-deck-controls {{
    right: calc(env(safe-area-inset-right, 0px) + 14px);
    top: max(24px, calc(100dvh - 178px));
    bottom: auto;
  }}
  html.newsly-learning-deck-responsive.newsly-learning-deck-portrait .reveal .controls {{
    bottom: 92px !important;
  }}
  html.newsly-learning-deck-landscape .newsly-learning-deck-controls {{
    right: calc(env(safe-area-inset-right, 0px) + 12px);
    bottom: calc(env(safe-area-inset-bottom, 0px) + 12px);
    gap: 6px;
  }}
  html.newsly-learning-deck-landscape .newsly-learning-deck-controls button {{
    width: 40px;
    height: 40px;
    font-size: 24px;
  }}
  html.newsly-learning-deck-landscape
    .newsly-learning-deck-controls button[data-newsly-learning-deck-fullscreen] {{
    font-size: 18px;
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
  .newsly-learning-deck-controls button[data-newsly-learning-deck-fullscreen] {{
    font-size: 20px;
  }}
  /* Deck navigation buttons follow the house light theme above. */
</style>
<div class="newsly-learning-deck-controls" {NAVIGATION_CONTROLS_MARKER}="controls">
  <button
    type="button"
    data-newsly-learning-deck-fullscreen
    aria-label="Toggle fullscreen"
  >&#x26F6;</button>
</div>
<script {NAVIGATION_CONTROLS_MARKER}="script">
(function () {{
  var isResponsiveDeck = {responsive_layout_js};
  var layoutProfiles = {layout_profiles_js};
  var fullscreenButton = document.querySelector("[data-newsly-learning-deck-fullscreen]");
  if (!fullscreenButton) return;
  var lastFullscreenActivationAt = 0;
  var relayoutDeck = function () {{}};

  function withReveal(callback) {{
    if (window.Reveal && typeof window.Reveal.configure === "function") {{
      callback(window.Reveal);
      return;
    }}
    window.setTimeout(function () {{ withReveal(callback); }}, 150);
  }}

  function setButtonAvailability(button, isAvailable) {{
    button.classList.toggle("is-unavailable", !isAvailable);
    button.setAttribute("aria-disabled", isAvailable ? "false" : "true");
  }}

  function fullscreenElement() {{
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }}

  function fullscreenEnabled() {{
    return Boolean(
      document.fullscreenEnabled ||
      document.webkitFullscreenEnabled ||
      document.documentElement.requestFullscreen ||
      document.documentElement.webkitRequestFullscreen
    );
  }}

  function syncFullscreenButton() {{
    var enabled = fullscreenEnabled();
    var active = Boolean(fullscreenElement());
    setButtonAvailability(fullscreenButton, enabled);
    fullscreenButton.setAttribute("aria-pressed", active ? "true" : "false");
    fullscreenButton.setAttribute(
      "aria-label",
      active ? "Exit fullscreen" : "Enter fullscreen"
    );
  }}

  function requestFullscreen() {{
    var target = document.documentElement;
    if (target.requestFullscreen) {{
      return target.requestFullscreen();
    }}
    if (target.webkitRequestFullscreen) {{
      return target.webkitRequestFullscreen();
    }}
    return null;
  }}

  function exitFullscreen() {{
    if (document.exitFullscreen) {{
      return document.exitFullscreen();
    }}
    if (document.webkitExitFullscreen) {{
      return document.webkitExitFullscreen();
    }}
    return null;
  }}

  function toggleFullscreen(event) {{
    if (event) {{
      event.preventDefault();
      event.stopPropagation();
    }}
    var now = Date.now();
    if (now - lastFullscreenActivationAt < 500) return;
    lastFullscreenActivationAt = now;
    if (!fullscreenEnabled()) {{
      syncFullscreenButton();
      return;
    }}
    var result = fullscreenElement() ? exitFullscreen() : requestFullscreen();
    if (result && typeof result.catch === "function") {{
      result.catch(function () {{ syncFullscreenButton(); }});
    }}
    window.setTimeout(function () {{
      syncFullscreenButton();
      relayoutDeck();
    }}, 150);
  }}

  function bindFullscreen(button) {{
    button.addEventListener("pointerdown", toggleFullscreen);
    button.addEventListener("touchstart", toggleFullscreen, {{ passive: false }});
    button.addEventListener("click", toggleFullscreen);
  }}

  bindFullscreen(fullscreenButton);
  syncFullscreenButton();
  document.addEventListener("fullscreenchange", function () {{
    syncFullscreenButton();
    relayoutDeck();
  }});
  document.addEventListener("webkitfullscreenchange", function () {{
    syncFullscreenButton();
    relayoutDeck();
  }});

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
      if (typeof reveal.configure === "function") {{
        reveal.configure(fitConfig());
      }}
      if (
        reveal.scrollView &&
        typeof reveal.scrollView.deactivate === "function"
      ) {{
        reveal.scrollView.deactivate();
      }}
      if (typeof reveal.layout === "function") {{
        reveal.layout();
      }}
      syncFullscreenButton();
    }}
    var fitAnimationFrame = null;
    function scheduleFit() {{
      if (fitAnimationFrame !== null) return;
      fitAnimationFrame = window.requestAnimationFrame(function () {{
        fitAnimationFrame = null;
        applyFit();
      }});
    }}
    relayoutDeck = scheduleFit;

    if (typeof reveal.configure === "function") {{
      reveal.configure({{
        controls: true,
        progress: false,
        minScale: 0.05,
        maxScale: 3,
        view: "slide",
        scrollActivationWidth: null
      }});
    }}
    if (typeof reveal.on === "function") {{
      reveal.on("ready", scheduleFit);
      reveal.on("slidechanged", scheduleFit);
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
