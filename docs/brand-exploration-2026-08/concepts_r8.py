"""Round 8: faithful recolors of the two finalists, plus two-tone treatments.

Round 7's Reader variants were wrong. Re-describing the mark in text drifted it back into the
small round blob from round 4 and lost the arched top and deep swallow-tail entirely. This
round edits from the original images instead, so the silhouette is held and only the color
changes. Every concept here carries a REFS entry.

Two halves:
  A. Faithful recolors of Ensō · Petrol (r6-03) and Reader × Ribbon Tail (r6-16).
  B. Two-tone treatments — exactly two flat inks, no shading, no gradients, no third value.
     A two-color screen-print constraint, which is also the cheapest thing to reproduce
     across print, favicon and a single-color app icon.
"""

from pathlib import Path

ROOT = Path(__file__).parent
ENSO_SRC = ROOT / "images_r6" / "r6-03-enso-petrol.png"
READER_SRC = ROOT / "images_r6" / "r6-16-blob-tail.png"

MODELS = {
    "nano-banana-pro": ("openrouter", "google/gemini-3-pro-image", "Nano Banana Pro"),
}

# Kept deliberately short: with a reference image the style base mostly gets in the way.
STYLE_BASE = (
    "Edit the supplied app icon mark. Keep the silhouette, proportions, composition and "
    "drawing EXACTLY as they are — do not redraw, restyle, resize or reposition anything, and "
    "do not add or remove any element. Change only what is described below. Keep it a flat 2D "
    "illustration, centered on a plain flat field, with no text and no added decoration. "
)

RECOLOR_PALETTES = [
    ("ink", "Ink", "near-black ink", "warm cream", "soft oat"),
    ("indigo", "Indigo", "deep indigo", "pale gold", "warm cream"),
    ("slate", "Slate", "deep slate blue", "warm cream", "pale ivory"),
    ("forest", "Forest", "deep forest green", "warm oat", "pale cream"),
    ("espresso", "Espresso", "deep espresso brown", "soft blush", "warm cream"),
    ("oxblood", "Oxblood", "deep oxblood red", "warm oat", "pale cream"),
    ("plum", "Plum", "deep plum", "soft blush", "warm cream"),
    ("olive", "Olive", "dark olive", "warm cream", "warm oat"),
]

# Two-tone: exactly two inks, everything else knocked out to the paper.
TWOTONE_PALETTES = [
    ("ink-cream", "Ink / Cream", "near-black", "warm cream"),
    ("petrol-sand", "Petrol / Sand", "deep petrol blue-green", "warm sand"),
    ("indigo-gold", "Indigo / Gold", "deep indigo", "soft muted gold"),
    ("forest-oat", "Forest / Oat", "deep forest green", "warm oat"),
    ("oxblood-blush", "Oxblood / Blush", "deep oxblood red", "soft blush"),
    ("slate-ivory", "Slate / Ivory", "deep slate blue", "pale ivory"),
]

CONCEPTS: list[tuple[str, str, str, str, str]] = []
REFS: dict[str, Path] = {}


def _add(cid: str, title: str, desc: str, prompt: str, ref: Path) -> None:
    CONCEPTS.append((cid, "nano-banana-pro", title, desc, prompt))
    REFS[cid] = ref


# ---- A. faithful recolors ----
for i, (slug, name, dom, acc, ground) in enumerate(RECOLOR_PALETTES, start=1):
    _add(
        f"r8-{i:02d}-enso-{slug}",
        f"Ensō · {name}",
        f"Finalist one recolored to {name.lower()}, edited from the original so the brush ring "
        "is unchanged.",
        STYLE_BASE
        + f"Recolor only: make the brushed ring {dom}, the open book {acc}, and the "
        f"background {ground}. Keep the brush texture and every edge identical.",
        ENSO_SRC,
    )
for i, (slug, name, dom, acc, ground) in enumerate(RECOLOR_PALETTES, start=9):
    _add(
        f"r8-{i:02d}-reader-{slug}",
        f"Reader · {name}",
        f"Finalist two recolored to {name.lower()}. Edited from the original, so the arched top "
        "and deep swallow-tail survive this time.",
        STYLE_BASE
        + f"Recolor only: make the body {dom}, the round spectacles {acc}, and the "
        f"background {ground}. Keep the arched top, the deep V notch at the bottom and the "
        "spectacles exactly as drawn.",
        READER_SRC,
    )

# ---- B. two-tone ----
for i, (slug, name, dom, acc) in enumerate(TWOTONE_PALETTES, start=17):
    _add(
        f"r8-{i:02d}-enso-2t-{slug}",
        f"Ensō 2-tone · {name}",
        f"Two flat inks only — {name.lower()} — with all shading removed. A screen-print "
        "reduction of the ring.",
        STYLE_BASE
        + "Convert this to a strict TWO-TONE mark: exactly two flat colors and "
        f"nothing else. The brushed ring becomes solid {dom} with no shading, no gradient and "
        f"no tonal variation. The background and the open book become flat {acc}. Every soft "
        "shadow, highlight and mid-tone must be removed — only two flat values may remain.",
        ENSO_SRC,
    )
for i, (slug, name, dom, acc) in enumerate(TWOTONE_PALETTES, start=23):
    _add(
        f"r8-{i:02d}-reader-2t-{slug}",
        f"Reader 2-tone · {name}",
        f"The same reduction on the reader — {name.lower()}, two inks, no shading.",
        STYLE_BASE
        + "Convert this to a strict TWO-TONE mark: exactly two flat colors and "
        f"nothing else. The body becomes solid {dom} with no shading, no gradient and no fold "
        f"detail. The background, the spectacles and the eyes become flat {acc}. Every soft "
        "shadow and mid-tone must be removed — only two flat values may remain. Keep the arched "
        "top and the deep V notch exactly as drawn.",
        READER_SRC,
    )
